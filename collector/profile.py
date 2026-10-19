"""Read each mosque website's profile: Jumu'ah times, sisters' facilities, other facilities.

    python -m collector.profile --limit 200      # each mosque's profile, read ONCE
    python -m collector.profile --jumuah         # weekly: re-check Jumu'ah times only

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
MAX_PAGES = 6
JUM = re.compile(r"jum[u']?'?a?h|jumm?a|jumu|friday|khutb", re.I)
HAS_TIME = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
MEDIA = re.compile(r"\.(pdf|png|jpe?g|webp)(\?|$)", re.I)

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
            keep.update(range(max(0, i - 3), min(len(lines), i + 8)))
    out = "\n".join(lines[i] for i in sorted(keep))
    return out[:limit]


def site_pages(m: dict, start: str, extra: list[str] | None = None, render_first: bool = False):
    """([(url, relevant text)], combined text, [Jumu'ah poster/PDF urls]) for the home page, the
    mosque's timetable page and the most relevant linked pages."""
    site = urlparse(start).netloc.lower().removeprefix("www.")
    got, queue, seen, media = [], [start] + [u for u in (extra or []) if u and u != start], set(), []
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
        jum_lines = [l for l in text.split("\n") if JUM.search(l)]
        needs_browser = len(text) < 400 or render_first or (jum_lines and not HAS_TIME.search(relevant(text)))
        if needs_browser:                         # page fills in its content with JavaScript
            try:
                body = fetch.render(url)
                text, _ = page_text(body, url)
            except (fetch.Blocked, fetch.FetchError):
                pass
        # Jumu'ah times posted as a poster image or PDF
        for tag in soup_of(body).find_all(["img", "a"]):
            src = tag.get("src") or tag.get("href") or ""
            label = " ".join(filter(None, [tag.get("alt"), tag.get("title"), tag.get_text(" ", strip=True) if tag.name == "a" else "", src]))
            if src and JUM.search(label) and (tag.name == "img" or MEDIA.search(src)):
                u = urljoin(url, src)
                if u not in media and not u.startswith("data:"):
                    media.append(u)
        rel = relevant(text)
        if rel:
            got.append((url, rel))
        if url == start or len(seen) <= 2:
            scored = []
            for a in soup_of(body).find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#")[0]
                if NOT_PAGES.search(href) or urlparse(href).netloc.lower().removeprefix("www.") != site:
                    continue
                hay = a.get_text(" ", strip=True) + " " + urlparse(href).path
                score = (3 if re.search(r"jum|friday|khutb", hay, re.I) else 0) + \
                        (2 if re.search(r"sister|women|ladies|facilit", hay, re.I) else 0) + \
                        (1 if re.search(r"about|visit|contact|prayer|service|amenit|our-mosque|centre", hay, re.I) else 0)
                if score:
                    scored.append((score, href))
            for _, h in sorted(scored, key=lambda x: -x[0]):
                if h not in queue and h not in seen:
                    queue.append(h)
    combined = "\n\n".join(f"[page {urlparse(u).path or '/'}]\n{t}" for u, t in got)[:20000]
    return got, combined, media[:3]


POSTER_PROMPT = """This image/PDF is from the website of a UK mosque ({name}). If it shows Friday (Jumu'ah) prayer times,
return ONLY JSON {{"slots": [{{"name": "1st Jumu'ah", "khutbah": "13:15", "jamaat": "13:30"}}], "note": null}}; if the times change
with the clocks, give the ones that apply on {today}. If it shows no Jumu'ah times return {{"slots": []}}."""


def read_poster(m: dict, url: str, reader: Reader, today):
    from .extract import media_type_for
    try:
        data, ctype, _ = fetch.get(url)
    except (fetch.Blocked, fetch.FetchError):
        return None
    mt = media_type_for(url, ctype)
    if not mt or len(data) > 5_000_000:
        return None
    import base64
    block = {"type": "document" if mt == "application/pdf" else "image",
             "source": {"type": "base64", "media_type": mt, "data": base64.standard_b64encode(data).decode()}}
    reader._count()
    msg = reader.client.messages.create(model=config.CLAUDE_MODEL, max_tokens=500, messages=[{"role": "user", "content": [
        block, {"type": "text", "text": POSTER_PROMPT.format(name=m["name"], today=today)}]}])
    from .extract import parse_reply
    return clean_jumuah(parse_reply("".join(b.text for b in msg.content if getattr(b, "type", "") == "text")))


def read_profile(m: dict, start: str, reader: Reader, last_hash: str | None, today,
                 timetable_url: str | None = None, render_timetable: bool = False) -> dict:
    """Returns fields to save on the mosque (may be just profile_checked_at when nothing changed)."""
    now = datetime.now(timezone.utc).isoformat()
    pages, text, media = site_pages(m, start, [timetable_url] if timetable_url else None)
    if render_timetable and timetable_url and not any(u == timetable_url for u, _ in pages):
        try:
            t2 = relevant(page_text(fetch.render(timetable_url), timetable_url)[0])
            if t2:
                pages.append((timetable_url, t2))
                text = (text + f"\n\n[page {urlparse(timetable_url).path or '/'}]\n{t2}")[:20000]
        except (fetch.Blocked, fetch.FetchError):
            pass
    h = hashlib.sha256((re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", "", text) + "|".join(media)).encode()).hexdigest()
    if not text.strip():
        return {"profile_checked_at": now, "profile_hash": h}
    if h == last_hash:
        return {"profile_checked_at": now}
    ans = reader.ask(PROMPT.format(name=m["name"], where=where(m), today=today, text=text), max_tokens=800)
    src = urlparse(start).netloc.lower().removeprefix("www.")
    fields = {"profile_checked_at": now, "profile_at": now, "profile_hash": h, "profile_src": src}
    j = clean_jumuah(ans.get("jumuah"))
    poster = None
    if not j:
        for u in media:                           # Jumu'ah times posted as an image or PDF
            j = read_poster(m, u, reader, today)
            if j:
                poster = u
                break
    jum_url = next((u for u, t in pages if re.search(r"jum|friday|khutb", urlparse(u).path, re.I)), None) or \
        next((u for u, t in pages if re.search(r"khutb|jamaa?t", t, re.I) and re.search(r"jum|friday", t, re.I)), start)
    fields["jumuah"] = {**j, "src": src, "url": poster or jum_url, "at": now[:10]} if j else None
    fac = [str(x)[:40] for x in (ans.get("facilities") or []) if isinstance(x, str)][:8]
    fields["facilities"] = fac or None
    sis = ans.get("sisters")
    if sis in ("yes", "no"):
        fields["sisters_web"] = sis
        fields["sisters_web_note"] = (str(ans.get("sisters_note"))[:160] if ans.get("sisters_note") else None)
    return fields


JUMUAH_ONLY = """This is text from the Jumu'ah / Friday prayer page of a UK mosque: {name}, {where}.
Return ONLY JSON {{"slots": [{{"name": "1st Jumu'ah", "khutbah": "13:15", "jamaat": "13:30"}}], "note": "short note or null"}}.
24-hour HH:MM, null for parts not printed. If summer and winter times are both shown, give the ones for {today}
(UK clocks go back on the last Sunday of October, forward on the last Sunday of March). If none are shown: {{"slots": []}}.

TEXT:
{text}
"""


MANUAL = "manual"   # jumuah_hash value for Jumu'ah times entered by hand
CLOCK_WINDOW = 14   # days after a UK clock change during which every Jumu'ah is re-read


def last_sunday(year: int, month: int):
    from datetime import date
    d = date(year, month, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def recent_clock_change(today):
    """The UK clocks change on the last Sunday of March and October. Returns that date if it was
    within CLOCK_WINDOW days before today (Jumu'ah times usually move with the clocks), else None."""
    for d in (last_sunday(today.year, 10), last_sunday(today.year, 3)):
        if 0 <= (today - d).days <= CLOCK_WINDOW:
            return d
    return None


def refresh_jumuah(m: dict, reader: Reader, today) -> dict:
    """Weekly: re-read only the page the Jumu'ah times came from; AI only when that page changed."""
    now = datetime.now(timezone.utc).isoformat()
    j = m["jumuah"] or {}
    url = j.get("url")
    if not url or m.get("jumuah_hash") == MANUAL:
        return {"jumuah_checked_at": now}
    change = recent_clock_change(today)
    force = bool(change and (j.get("at") or "") < change.isoformat())   # read before the clocks changed
    if MEDIA.search(url):                                     # poster / PDF
        data_hash = hashlib.sha256(fetch.get(url)[0]).hexdigest()
        if not force and data_hash == m.get("jumuah_hash"):
            return {"jumuah_checked_at": now}
        new = read_poster(m, url, reader, today)
    else:
        body, ctype, _ = fetch.get(url)
        text = relevant(page_text(body, url)[0])
        if not HAS_TIME.search(text):
            try:
                text = relevant(page_text(fetch.render(url), url)[0])
            except (fetch.Blocked, fetch.FetchError):
                pass
        data_hash = hashlib.sha256(re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", "", text).encode()).hexdigest()
        if not force and data_hash == m.get("jumuah_hash"):
            return {"jumuah_checked_at": now}
        new = clean_jumuah(reader.ask(JUMUAH_ONLY.format(name=m["name"], where=where(m), today=today, text=text[:8000]),
                                      max_tokens=400))
    f = {"jumuah_checked_at": now, "jumuah_hash": data_hash}
    if new:
        f["jumuah"] = {**new, "src": j.get("src"), "url": url, "at": now[:10]}
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=config.WORKERS)
    ap.add_argument("--jumuah", action="store_true", help="weekly refresh of Jumu'ah times only")
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
    reader = Reader(max_calls=config.MAX_AI_CALLS_LEARN)
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.jumuah:
        change = recent_clock_change(today)
        stale = f",jumuah->>at.lt.{change.isoformat()}" if change else ""
        if change:
            print(f"Clocks changed on {change}: re-reading every Jumu'ah read before then")
        rows = store._req("GET", "mosques?select=id,name,address,town,postcode,jumuah,jumuah_hash&active=is.true"
                                 f"&jumuah=not.is.null"
                                 f"&or=(jumuah_checked_at.is.null,jumuah_checked_at.lt.{week_ago}{stale})"
                                 "&order=jumuah_checked_at.asc.nullsfirst", headers={"Range": f"0-{args.limit - 1}"})
        changed, log = 0, []

        def jone(m):
            try:
                f = refresh_jumuah(m, reader, today)
            except Exception as e:
                return f"{m['name']}: {str(e)[:100]}"
            store._req("PATCH", f"mosques?id=eq.{m['id']}", json=f, headers={"Prefer": "return=minimal"})
            return f"{m['name']}: " + ("updated " + ", ".join(s.get("jamaat") or s.get("khutbah") for s in f["jumuah"]["slots"])
                                       if f.get("jumuah") else "unchanged")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            log = [f.result() for f in as_completed([pool.submit(jone, m) for m in rows])]
        print(f"## Jumu'ah refresh\n\n{len(rows)} checked · AI calls: {reader.calls}\n\n" + "\n".join(f"- {l}" for l in sorted(log)))
        return
    mosques = store._req("GET", "mosques?select=id,name,address,town,postcode,website,profile_hash,jumuah_hash,sisters_web,sources(url,recipe)"
                                "&active=is.true&website=not.is.null&profile_checked_at=is.null"
                                "&order=profile_priority.desc,id",
                         headers={"Range": f"0-{args.limit - 1}"})
    counts, log = {"jumuah": 0, "sisters": 0, "unchanged": 0, "nothing": 0, "blocked": 0, "error": 0}, []

    def one(m):
        try:
            src = next((s for s in (m.get("sources") or []) if s.get("url")), None)
            f = read_profile(m, m["website"], reader, m.get("profile_hash"), today,
                             timetable_url=src["url"] if src else None,
                             render_timetable=bool(src and (src.get("recipe") or {}).get("render")))
        except fetch.Blocked as e:
            store._req("PATCH", f"mosques?id=eq.{m['id']}", json={"profile_checked_at": datetime.now(timezone.utc).isoformat()},
                       headers={"Prefer": "return=minimal"})
            return "blocked", f"{m['name']}: website refuses automated visits"
        except Exception as e:
            return "error", f"{m['name']}: {str(e)[:120]}"
        if m.get("jumuah_hash") == MANUAL:          # times given to us by hand: never overwrite them
            f.pop("jumuah", None)
            if m.get("sisters_web"):
                f.pop("sisters_web", None)
                f.pop("sisters_web_note", None)
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
