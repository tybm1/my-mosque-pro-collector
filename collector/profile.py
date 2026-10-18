"""Read each mosque website's profile: Jumu'ah times, sisters' facilities, other facilities.

    python -m collector.profile --limit 200

For each mosque with a website (checked weekly, newest first):
  1. read the home page plus up to 4 pages about Jumu'ah / Friday prayer,
     facilities, sisters, about / visiting (opened in a browser when the plain
     page has almost no text)
  2. keep only the parts that mention those things (cheap)
  3. if that text changed since last week, Claude reads it and returns the
     Jumu'ah slots (khutbah / jamaat times), whether there is a sisters' area,
     and the facilities listed
  4. Jumu'ah times are checked to be between 11:30 and 15:30 before saving
The app shows these on the mosque page, with the website as the source.
"""
import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from . import config, fetch
from .extract import Reader, page_text, where
from .recipes import soup_of

PROFILE_WORDS = re.compile(r"jum[u']?'?[a]?h?|jumm?ah|jumu|friday|khutb|sister|women|ladies|female|facilit|wudu|wudhu|"
                           r"ablution|parking|wheelchair|disab|access|about|visit|contact", re.I)
NOT_PAGES = re.compile(r"donat|zakat|login|account|cart|shop|wp-admin|facebook|twitter|instagram|whatsapp|mailto:|tel:|"
                       r"\.(pdf|png|jpe?g|webp|zip|mp[34])(\?|$)", re.I)
MAX_PAGES = 5

PROMPT = """These are extracts from the website of a UK mosque: {name}, {where}.
If the site covers several mosques or centres, answer ONLY for this one (match by name or street).

Return ONLY JSON:
{{"jumuah": {{"slots": [{{"name": "1st Jumu'ah", "khutbah": "13:15", "jamaat": "13:30"}}], "note": "short note, e.g. language or 'times change with the clocks'"}} or null,
  "sisters": "yes" | "no" | "unknown",
  "sisters_note": "short, e.g. 'Sisters' prayer hall on the first floor'" or null,
  "facilities": ["Wudu area", "Parking", "Wheelchair access", ...]}}

Rules:
- Jumu'ah: only times printed for Friday prayer (khutbah / jamaat / adhan of Jumu'ah). 24-hour HH:MM. Use null for a part not printed. If no Jumu'ah times are printed, "jumuah": null.
- If summer and winter Jumu'ah times are both printed, give the ones that apply on {today} (UK clocks go back on the last Sunday of October) and mention the other in the note.
- "sisters": "yes" only if the text says there is a women's / sisters' prayer area or that sisters are welcome to pray; "no" only if it says there isn't; otherwise "unknown".
- facilities: only things the text clearly states (max 8, short names). Never guess.

EXTRACTS:
{text}
"""

TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def jumuah_minutes(t):
    m = TIME.match(str(t or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h < 10:                       # '1:30' means 13:30
        h += 12
    v = h * 60 + mi
    return v if 11 * 60 + 30 <= v <= 15 * 60 + 30 else None


def clean_jumuah(j):
    """Keep only plausible Friday times; returns None if nothing usable."""
    if not isinstance(j, dict):
        return None
    slots = []
    for s in (j.get("slots") or [])[:4]:
        if not isinstance(s, dict):
            continue
        k, q = jumuah_minutes(s.get("khutbah")), jumuah_minutes(s.get("jamaat"))
        if k is None and q is None:
            continue
        f = lambda v: None if v is None else f"{v // 60:02d}:{v % 60:02d}"
        slots.append({"name": str(s.get("name") or f"Jumu'ah {len(slots) + 1}")[:30], "khutbah": f(k), "jamaat": f(q)})
    if not slots:
        return None
    return {"slots": slots, "note": (str(j.get("note"))[:140] if j.get("note") else None)}


def relevant(text: str, limit: int = 6000) -> str:
    """The lines that mention Jumu'ah, sisters or facilities, with a little context."""
    lines = [l for l in text.split("\n") if l.strip()]
    keep = set()
    for i, l in enumerate(lines):
        if PROFILE_WORDS.search(l):
            keep.update(range(max(0, i - 2), min(len(lines), i + 4)))
    out = "\n".join(lines[i] for i in sorted(keep))
    return out[:limit]


def site_pages(m: dict, start: str) -> tuple[list[tuple[str, str]], str]:
    """[(url, relevant text)] for the home page and the most relevant linked pages."""
    site = urlparse(start).netloc.lower().removeprefix("www.")
    got, queue, seen = [], [start], set()
    while queue and len(got) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            body, ctype, _ = fetch.get(url)
        except fetch.Blocked:
            if url == start:
                raise
            continue
        except fetch.FetchError:
            continue
        if "html" not in ctype:
            continue
        text, _ = page_text(body, url)
        if len(text) < 400:                       # page built by JavaScript
            try:
                body = fetch.render(url)
                text, _ = page_text(body, url)
            except (fetch.Blocked, fetch.FetchError):
                pass
        rel = relevant(text)
        if rel:
            got.append((url, rel))
        if url == start:
            scored = []
            for a in soup_of(body).find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#")[0]
                if NOT_PAGES.search(href) or urlparse(href).netloc.lower().removeprefix("www.") != site:
                    continue
                hay = a.get_text(" ", strip=True) + " " + urlparse(href).path
                score = (3 if re.search(r"jum|friday|khutb", hay, re.I) else 0) + \
                        (2 if re.search(r"sister|women|ladies|facilit", hay, re.I) else 0) + \
                        (1 if re.search(r"about|visit|contact|prayer", hay, re.I) else 0)
                if score:
                    scored.append((score, href))
            for _, h in sorted(scored, key=lambda x: -x[0]):
                if h not in queue and h not in seen:
                    queue.append(h)
    combined = "\n\n".join(f"[page {urlparse(u).path or '/'}]\n{t}" for u, t in got)[:20000]
    return got, combined


def read_profile(m: dict, start: str, reader: Reader, last_hash: str | None, today) -> dict:
    """Returns fields to save on the mosque (may be just profile_checked_at when nothing changed)."""
    now = datetime.now(timezone.utc).isoformat()
    pages, text = site_pages(m, start)
    h = hashlib.sha256(re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", "", text).encode()).hexdigest()
    if not text.strip():
        return {"profile_checked_at": now, "profile_hash": h}
    if h == last_hash:
        return {"profile_checked_at": now}
    ans = reader.ask(PROMPT.format(name=m["name"], where=where(m), today=today, text=text), max_tokens=800)
    src = urlparse(start).netloc.lower().removeprefix("www.")
    fields = {"profile_checked_at": now, "profile_at": now, "profile_hash": h, "profile_src": src}
    j = clean_jumuah(ans.get("jumuah"))
    jum_url = next((u for u, t in pages if re.search(r"jum|friday|khutb", urlparse(u).path, re.I)), None) or \
        next((u for u, t in pages if re.search(r"khutb|jamaa?t", t, re.I) and re.search(r"jum|friday", t, re.I)), start)
    fields["jumuah"] = {**j, "src": src, "url": jum_url, "at": now[:10]} if j else None
    fac = [str(x)[:40] for x in (ans.get("facilities") or []) if isinstance(x, str)][:8]
    fields["facilities"] = fac or None
    sis = ans.get("sisters")
    if sis in ("yes", "no"):
        fields["sisters_web"] = sis
        fields["sisters_web_note"] = (str(ans.get("sisters_note"))[:160] if ans.get("sisters_note") else None)
    return fields


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=config.WORKERS)
    ap.add_argument("--try", dest="try_url", metavar="URL", help="read one website and print the profile (nothing saved)")
    args = ap.parse_args()
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    if args.try_url:
        r = Reader(max_calls=3)
        m = {"name": urlparse(args.try_url).netloc, "town": "UK"}
        try:
            print(json.dumps(read_profile(m, args.try_url, r, None, today), indent=2))
        except fetch.Blocked as e:
            print(f"website refuses automated visits — {e}")
        return
    from .store import Store
    store = Store()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mosques = store._req("GET", "mosques?select=id,name,address,town,postcode,website,profile_hash"
                                f"&active=is.true&website=not.is.null"
                                f"&or=(profile_checked_at.is.null,profile_checked_at.lt.{week_ago})"
                                "&order=profile_priority.desc,profile_checked_at.asc.nullsfirst,id",
                         headers={"Range": f"0-{args.limit - 1}"})
    reader = Reader(max_calls=config.MAX_AI_CALLS_LEARN)
    counts, log = {"jumuah": 0, "sisters": 0, "unchanged": 0, "nothing": 0, "blocked": 0, "error": 0}, []

    def one(m):
        try:
            f = read_profile(m, m["website"], reader, m.get("profile_hash"), today)
        except fetch.Blocked as e:
            store._req("PATCH", f"mosques?id=eq.{m['id']}", json={"profile_checked_at": datetime.now(timezone.utc).isoformat()},
                       headers={"Prefer": "return=minimal"})
            return "blocked", f"{m['name']}: website refuses automated visits"
        except Exception as e:
            return "error", f"{m['name']}: {str(e)[:120]}"
        store._req("PATCH", f"mosques?id=eq.{m['id']}", json=f, headers={"Prefer": "return=minimal"})
        if "profile_at" not in f:
            return "unchanged", f"{m['name']}: unchanged"
        bits = []
        if f.get("jumuah"):
            bits.append("Jumu'ah " + ", ".join(s.get("jamaat") or s.get("khutbah") for s in f["jumuah"]["slots"]))
        if f.get("sisters_web"):
            bits.append(f"sisters: {f['sisters_web']}")
        if f.get("facilities"):
            bits.append(f"{len(f['facilities'])} facilities")
        kind = "jumuah" if f.get("jumuah") else ("sisters" if f.get("sisters_web") else "nothing")
        return kind, f"{m['name']}: " + ("; ".join(bits) or "nothing found")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(one, m) for m in mosques]):
            k, line = fut.result()
            counts[k] = counts.get(k, 0) + 1
            log.append(line)
    summary = (f"## Mosque profiles (Jumu'ah, sisters, facilities)\n\n" + " · ".join(f"{k}: {v}" for k, v in counts.items())
               + f" · AI calls: {reader.calls}\n\n" + "\n".join(f"- {l}" for l in sorted(log)[:600]))
    print(summary)
    import os
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(summary + "\n")


if __name__ == "__main__":
    main()
