"""Per-website "recipes": how to read one mosque's timetable without AI.

Claude looks at a mosque's timetable page once (collector.learn) and writes down
where the jamaat times are — which table and columns, or which elements on the
page. Every day after that the collector follows the recipe with plain code, so
reading 1,000+ mosques costs nothing. If a site changes and the recipe stops
giving sensible times, the collector falls back to an AI read that day and the
recipe is learned again.

Recipe kinds
  table     a dated table (monthly / yearly timetable) on a web page or in a PDF
  fields    a "today's times" box: one element per prayer
  mawaqit   the mosque's Mawaqit screen page (structured data)
  page_ai   jamaat times are on this page but not in a regular layout: AI reads it when it changes
  file_ai   a PDF/photo timetable (scanned, or no usable table): AI reads it when it changes
  none      no jamaat times found on the site (checked again monthly)
"""
import calendar
import io
import json
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

PRAYER_KEYS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
TIME = re.compile(r"(?<![\d:.])(\d{1,2})\s*[:.]\s*(\d{2})(?![\d])")
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
MONTH_YEAR = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?,?\s+(20\d\d)\b", re.I)
TIMETABLE_FILE = re.compile(r"\.(pdf|png|jpe?g|webp)(\?|$)", re.I)
PRAYER_WORDS = {
    "fajr": r"fajr|fajar|fajir|subh|sobh",
    "dhuhr": r"dhuhr|zuhr|zohr|duhr|dhuhur|zuhar|johr|zohar",
    "asr": r"asr|asar",
    "maghrib": r"maghrib|magrib|maghrb",
    "isha": r"isha|esha|ishaa",
    "jumuah": r"jum|friday",
}


class RecipeError(Exception):
    """The recipe no longer fits the page (site changed) — relearn it."""


# ──────────────────────────── page helpers ────────────────────────────

def soup_of(html: bytes | str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return soup


def times_in(text: str) -> list[str]:
    return [f"{int(h)}:{m}" for h, m in TIME.findall(text or "") if int(h) < 24 and int(m) < 60]


def cell_texts(tr) -> list[str]:
    """Cells of a table row, with colspan expanded so column numbers stay stable."""
    out = []
    for c in tr.find_all(["td", "th"], recursive=False):
        t = c.get_text(" ", strip=True)
        try:
            span = max(1, min(int(c.get("colspan", 1)), 20))
        except ValueError:
            span = 1
        out.extend([t] * span)
    return out


def table_rows(table) -> list[list[str]]:
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:      # skip rows of nested tables
            continue
        rows.append(cell_texts(tr))
    return rows


def signature(rows: list[list[str]]) -> str:
    """Header words of a table, used to find the same table again if the page moves things around."""
    head = " ".join(" ".join(r) for r in rows[:2]).lower()
    return " ".join(sorted(set(re.findall(r"[a-z]{3,}", head))))[:200]


def css_path(el) -> str:
    """A selector for this exact element (id where there is one, else positions)."""
    parts = []
    while el is not None and el.name not in (None, "[document]", "html"):
        eid = el.get("id")
        if eid:
            parts.append(f'{el.name}[id="{eid}"]')
            break
        idx = 1 + sum(1 for s in el.find_previous_siblings(el.name))
        parts.append(f"{el.name}:nth-of-type({idx})")
        el = el.parent
    return " > ".join(reversed(parts))


def context_of(el) -> str:
    """The row or block an element sits in — usually holds the prayer name and 'begins'/'jamaat' labels."""
    tr = el.find_parent("tr")
    if tr is not None:
        rows = table_rows(tr.find_parent("table")) if tr.find_parent("table") else []
        head = " | ".join(rows[0]) if rows else ""
        return (" | ".join(cell_texts(tr)) + ("   [table header: " + head + "]" if head else ""))[:220]
    node = el
    for _ in range(3):
        if node.parent is None:
            break
        node = node.parent
        txt = node.get_text(" | ", strip=True)
        if len(txt) > 25:
            return txt[:220]
    return node.get_text(" | ", strip=True)[:220]


def month_on_page(soup_or_text, today: date) -> tuple[int, int] | None:
    text = soup_or_text if isinstance(soup_or_text, str) else soup_or_text.get_text(" ", strip=True)
    m = MONTH_YEAR.search(text or "")
    if m:
        return int(m.group(2)), MONTHS[m.group(1).lower().rstrip(".")]
    return None


def table_month(table, soup, today: date) -> tuple[int, int] | None:
    """Month a day-of-month table is for: its caption, the heading just before it, its own header, then the page."""
    cap = table.find("caption")
    for src in ([cap.get_text(" ")] if cap else []) + \
               [h.get_text(" ") for h in table.find_all_previous(["h1", "h2", "h3", "h4", "h5", "strong", "p"], limit=6)] + \
               [" ".join(" ".join(r) for r in table_rows(table)[:3])]:
        ym = month_on_page(src, today)
        if ym:
            return ym
    return month_on_page(soup, today)


# ──────────────────────────── dates ────────────────────────────

DMY = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b")
D_MON = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\b(?:,?\s*(20\d\d))?", re.I)
MON_D = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(20\d\d))?", re.I)


def _year_for(month: int, today: date) -> int:
    """Timetables without a year: a month far behind today is next year's."""
    if month < today.month - 6:
        return today.year + 1
    if month > today.month + 6:
        return today.year - 1
    return today.year


def parse_date(text: str, style: str, ctx: tuple[int, int] | None, today: date) -> date | None:
    t = (text or "").strip()
    if not t:
        return None
    if style == "full_date":
        for rx, order in ((D_MON, "dm"), (MON_D, "md")):
            m = rx.search(t)
            if m:
                if order == "dm":
                    d, mon, y = int(m.group(1)), MONTHS[m.group(2).lower().rstrip(".")], m.group(3)
                else:
                    mon, d, y = MONTHS[m.group(1).lower().rstrip(".")], int(m.group(2)), m.group(3)
                y = int(y) if y else _year_for(mon, today)
                try:
                    return date(y, mon, d)
                except ValueError:
                    return None
        m = DMY.search(t)
        if m:                                   # UK order: day/month/year
            d, mon, y = int(m.group(1)), int(m.group(2)), m.group(3)
            y = (int(y) + 2000 if len(y) == 2 else int(y)) if y else _year_for(mon, today)
            try:
                return date(y, mon, d)
            except ValueError:
                return None
        style = "day_of_month"
    m = re.match(r"\D{0,12}?(\d{1,2})\b", t)
    if not m:
        return None
    d = int(m.group(1))
    if not 1 <= d <= 31:
        return None
    y, mon = ctx or (today.year, today.month)
    try:
        return date(y, mon, d)
    except ValueError:
        return None


# ──────────────────────────── applying recipes ────────────────────────────

def _pick(text: str, nth: int) -> str | None:
    ts = times_in(text)
    if not ts:
        return None
    return ts[nth] if -len(ts) <= nth < len(ts) else None


def rows_to_days(rows: list[list[str]], spec: dict, ctx: tuple[int, int] | None, today: date) -> dict:
    """Apply a table spec to rows of cell text → {iso_date: {prayer: 'H:MM'}}."""
    cols = spec["cols"]
    style = spec.get("date_style", "day_of_month")
    dcol = spec["date_col"]
    days, last = {}, None
    for row in rows[spec.get("header_rows", 1):]:
        if dcol >= len(row):
            continue
        d = parse_date(row[dcol], style, ctx, today)
        if d is None:
            continue
        if style == "day_of_month" and last is not None and d <= last:
            # day numbers went back (e.g. 30, 31, 1): next month
            nxt = (last.replace(day=1) + timedelta(days=32)).replace(day=1)
            try:
                d = date(nxt.year, nxt.month, d.day)
            except ValueError:
                continue
        last = d
        out = {}
        for key, c in cols.items():
            if not c or c.get("col") is None or c["col"] >= len(row):
                continue
            cell = row[c["col"]]
            out[key] = cell[:60] if key == "jumuah" and cell else _pick(cell, c.get("nth", 0))
        if any(out.get(p) for p in PRAYER_KEYS):
            days[d.isoformat()] = out
    return days


def apply_table_html(recipe: dict, html: bytes, today: date) -> dict:
    soup = soup_of(html)
    tables = soup.find_all("table")
    t = recipe["table"]
    cand = tables[t["index"]] if t["index"] < len(tables) else None
    if cand is None or (t.get("sig") and signature(table_rows(cand)) != t["sig"]):
        cand = next((x for x in tables if t.get("sig") and signature(table_rows(x)) == t["sig"]), None)
    if cand is None:
        raise RecipeError("timetable table not found on the page any more")
    ctx = table_month(cand, soup, today)
    days = rows_to_days(table_rows(cand), t, ctx, today)
    if not days:
        raise RecipeError("table found but no dated rows")
    return days


def pdf_tables(data: bytes) -> tuple[list[list[list[str]]], str]:
    """All tables in a PDF (pdfplumber) and its text."""
    import pdfplumber
    tables, text = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:4]:
            text.append(page.extract_text() or "")
            for tb in page.extract_tables():
                tables.append([[(c or "").replace("\n", " ").strip() for c in r] for r in tb if r])
    return tables, "\n".join(text)


def apply_table_pdf(recipe: dict, data: bytes, today: date, link_label: str = "") -> dict:
    tables, text = pdf_tables(data)
    t = recipe["table"]
    cand = tables[t["index"]] if t["index"] < len(tables) else None
    if cand is None or (t.get("sig") and signature(cand) != t["sig"]):
        cand = next((x for x in tables if t.get("sig") and signature(x) == t["sig"]), None)
    if cand is None:
        raise RecipeError("timetable table not found in the PDF")
    ctx = month_on_page(text, today) or month_on_page(link_label, today)
    days = rows_to_days(cand, t, ctx, today)
    if not days:
        raise RecipeError("PDF table found but no dated rows")
    return days


def apply_fields(recipe: dict, html: bytes, today: date) -> dict:
    soup = soup_of(html)
    out = {}
    for key, f in recipe["fields"].items():
        if not f:
            continue
        el = soup.select_one(f["sel"])
        if el is None:
            raise RecipeError(f"{key}: element not found any more")
        if f.get("label") and not re.search(re.escape(f["label"]), context_of(el), re.I):
            raise RecipeError(f"{key}: page layout changed")
        txt = el.get_text(" ", strip=True)
        out[key] = txt[:60] if key == "jumuah" else _pick(txt, f.get("nth", 0))
    if not any(out.get(p) for p in PRAYER_KEYS):
        raise RecipeError("no times in the expected places")
    return {today.isoformat(): out}


MAWAQIT_LINK = re.compile(r"mawaqit\.net/(?:[a-z]{2}/)?(?:(?:w|m|id)/)?([a-z0-9][a-z0-9-]{2,80})", re.I)
MAWAQIT_NOT_SLUGS = {"en", "fr", "ar", "de", "api", "static", "build", "images", "css", "js", "backoffice",
                     "login", "register", "about", "privacy", "terms", "faq", "contact", "mosques", "search"}


def mawaqit_slug(html: bytes | str) -> str | None:
    text = html.decode("utf-8", "ignore") if isinstance(html, bytes) else html
    for m in MAWAQIT_LINK.finditer(text):
        slug = m.group(1).lower()
        if slug not in MAWAQIT_NOT_SLUGS:
            return slug
    return None


def apply_mawaqit(page: bytes, today: date, days_ahead: int = 30) -> dict:
    """Read jamaat (iqama) times from a Mawaqit mosque page's embedded data.
    calendar[month][day] = [fajr, shuruq, dhuhr, asr, maghrib, isha] (start times);
    iqamaCalendar[month][day] = 5 values, each '+10' (minutes after start) or '06:30'."""
    text = page.decode("utf-8", "ignore")
    m = re.search(r"confData\s*=\s*(\{.*?\})\s*;\s*(?:\n|var |let |const |</script>)", text, re.S)
    if not m:
        raise RecipeError("Mawaqit page data not found")
    try:
        conf = json.loads(m.group(1))
        cal, iq = conf["calendar"], conf["iqamaCalendar"]
    except (ValueError, KeyError, TypeError):
        raise RecipeError("Mawaqit data in an unexpected format")
    if conf.get("iqamaEnabled") is False:
        raise RecipeError("this mosque has jamaat times switched off on Mawaqit")
    jum = ", ".join(x for x in (conf.get("jumua"), conf.get("jumua2"), conf.get("jumua3")) if x) or None
    days = {}
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        try:
            starts = cal[d.month - 1][str(d.day)]
            iqs = iq[d.month - 1][str(d.day)]
        except (IndexError, KeyError, TypeError):
            continue
        starts = [starts[0], starts[2], starts[3], starts[4], starts[5]]   # drop shuruq
        row = {}
        for key, s, q in zip(PRAYER_KEYS, starts, iqs):
            q = str(q).strip()
            if re.fullmatch(r"\+?\d{1,3}", q):
                h, mi = map(int, s.split(":"))
                t = h * 60 + mi + int(q.lstrip("+"))
                row[key] = f"{t // 60 % 24}:{t % 60:02d}"
            else:
                row[key] = _pick(q, 0)
        if d.weekday() == 4 and jum:
            row["jumuah"] = jum
        days[d.isoformat()] = row
    if not days:
        raise RecipeError("Mawaqit calendar empty")
    return days


# ──────────────────────────── timetable file links ────────────────────────────

def link_words(href: str, label: str) -> list[str]:
    """Words that identify a mosque's timetable file link, minus the parts that change monthly."""
    s = (label + " " + href.rsplit("/", 1)[-1]).lower()
    s = re.sub(r"\.(pdf|png|jpe?g|webp)\b", " ", s)
    words = [w for w in re.findall(r"[a-z]{3,}", s) if w not in MONTHS and w not in {"http", "https", "www", "uploads", "content", "files"}]
    return list(dict.fromkeys(words))[:8]


def pick_file_link(soup: BeautifulSoup, base: str, words: list[str], today: date) -> tuple[str, str] | None:
    """The timetable file link on a page: matches the learned words best, prefers this month's."""
    from urllib.parse import urljoin
    month_names = {calendar.month_name[today.month].lower(), calendar.month_abbr[today.month].lower()}
    best, best_score = None, 0
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if not TIMETABLE_FILE.search(href):
            continue
        label = a.get_text(" ", strip=True)
        lw = set(link_words(href, label))
        score = len(lw & set(words)) * 2
        hay = (label + " " + href).lower()
        if any(mn in hay for mn in month_names):
            score += 3
        if str(today.year) in hay:
            score += 1
        if score > best_score:
            best, best_score = (href, label), score
    return best if best and best_score >= 2 else None
