"""Turn a fetched web page, PDF or photo into jamaat times, using Claude.

HTML pages are reduced to their visible text first (cheap). PDFs and images are
sent to Claude directly — it reads printed and scanned timetables.
"""
import base64
import hashlib
import json
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import config

TIMETABLE_WORDS = re.compile(r"timetable|time-table|prayer|salah|salat|namaz|jama'?a?h?t|iqam|calendar", re.I)
FILE_LINK = re.compile(r"\.(pdf|png|jpe?g|webp)(\?|$)", re.I)

PROMPT = """You are reading the published prayer timetable of a UK mosque ({name}, {town}).
Today is {today}. Extract the JAMAAT times (also called iqamah, congregation, jama'ah) —
NOT the start / begins / adhan times. Many timetables show both; use only the jamaat column.
If the page shows only start times and no jamaat times, return has_jamaat false.

Return ONLY JSON in exactly this shape:
{{"has_jamaat": true,
  "days": {{"YYYY-MM-DD": {{"fajr": "HH:MM", "dhuhr": "HH:MM", "asr": "HH:MM", "maghrib": "HH:MM", "isha": "HH:MM", "jumuah": "13:15, 14:00"}}}},
  "note": "one short line about anything unusual"}}

Rules:
- Use 24-hour times. Afternoon/evening prayers are after 12:00.
- Include every date the timetable covers from {today} onwards (up to 2 months).
- A range like "1–7 Nov: Isha 19:30" applies to every date in the range.
- Maghrib jamaat is often "sunset+5" or "after adhan": if no clock time is printed, use null.
- Use null for anything not shown. Never guess or calculate a time.
- "jumuah" only on Fridays, as printed (khutbah or jamaat times), else null.
- If no year is printed, assume the dates are in the current or next month around {today}.
"""


def page_text(html: bytes, base_url: str) -> tuple[str, list[str]]:
    """Visible text of an HTML page (tables kept as rows) and links to timetable files."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form", "iframe"]):
        tag.decompose()
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with(soup.new_string(" | ".join(cells) + "\n"))
    text = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n", strip=True))
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        label = a.get_text(" ", strip=True) + " " + href
        if FILE_LINK.search(href) and TIMETABLE_WORDS.search(label):
            links.append(href)
    return text[:60000], list(dict.fromkeys(links))[:3]


def fingerprint_text(text: str) -> str:
    """Fingerprint that ignores clocks/countdowns that change every minute on many pages."""
    stable = re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", "", text)
    return hashlib.sha256(stable.encode()).hexdigest()


def has_times(text: str) -> bool:
    return len(re.findall(r"\b\d{1,2}[:.]\d{2}\b", text)) >= 5


class Reader:
    """Wraps the Claude API; counts calls so a run can't overspend."""

    def __init__(self, max_calls: int | None = None):
        import threading
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.calls = 0
        self.max_calls = max_calls or config.MAX_AI_CALLS
        self._lock = threading.Lock()

    def _count(self):
        with self._lock:
            if self.calls >= self.max_calls:
                raise RuntimeError("AI call limit for this run reached")
            self.calls += 1

    def ask(self, prompt: str, max_tokens: int = 2000) -> dict:
        """Any question that should come back as JSON (used to learn recipes)."""
        self._count()
        msg = self.client.messages.create(model=config.CLAUDE_MODEL, max_tokens=max_tokens,
                                          messages=[{"role": "user", "content": prompt}])
        return parse_reply("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))

    def read(self, mosque: dict, today: date, *, text: str | None = None,
             data: bytes | None = None, media_type: str | None = None) -> dict:
        self._count()
        prompt = PROMPT.format(name=mosque["name"], town=mosque["town"], today=today.isoformat())
        content = []
        if data is not None:
            b64 = base64.standard_b64encode(data).decode()
            if media_type == "application/pdf":
                content.append({"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}})
            else:
                content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
            content.append({"type": "text", "text": prompt})
        else:
            content.append({"type": "text", "text": prompt + "\n\nPAGE TEXT:\n" + (text or "")})
        msg = self.client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": content}],
        )
        reply = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return parse_reply(reply)


def parse_reply(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return {"has_jamaat": False, "days": {}, "note": "no JSON in reply"}
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"has_jamaat": False, "days": {}, "note": "unreadable JSON"}
    if not isinstance(out, dict):
        return {"has_jamaat": False, "days": {}, "note": "unexpected reply"}
    if not isinstance(out.get("days"), dict):
        out["days"] = {}
    return out


def media_type_for(url: str, ctype: str) -> str | None:
    if ctype in ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"):
        return ctype
    u = url.lower().split("?")[0]
    for ext, mt in ((".pdf", "application/pdf"), (".png", "image/png"), (".jpg", "image/jpeg"),
                    (".jpeg", "image/jpeg"), (".webp", "image/webp")):
        if u.endswith(ext):
            return mt
    return None
