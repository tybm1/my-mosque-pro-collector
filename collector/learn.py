"""Learn each mosque website's timetable recipe.

    python -m collector.learn --limit 120

For every source that has no recipe yet (or whose recipe broke):
  1. start at the mosque's website and find its prayer-times page(s)
     (links mentioning prayer / timetable / salah / jamaat, timetable PDFs,
     an embedded Mawaqit screen)
  2. show Claude a compact outline of the page (tables, elements holding times,
     timetable links) and ask where the JAMAAT times are
  3. turn the answer into a recipe and run it with plain code
  4. accept the recipe only if its times pass the sunrise/sunset checks AND agree
     with Claude reading the same page directly (≥ 90% of times within a minute)
  5. save the recipe, the timetable URL and the first days of times

Runs daily in the workflow until every website has been learned, then only
picks up new sources, broken recipes, and sites with nothing found (monthly).
"""
import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from . import config, fetch
from .extract import TIMETABLE_WORDS, Reader, has_times, media_type_for, page_text, where
from .recipes import (PRAYER_KEYS, PRAYER_WORDS, RecipeError, apply_fields, apply_mawaqit, apply_table_html,
                      apply_table_pdf, context_of, css_path, link_words, mawaqit_slug, pdf_tables, signature,
                      soup_of, table_rows, times_in)
from .validate import check_timetable, to_minutes

MAX_PAGES_PER_SITE = 6
MAX_RENDERS_PER_SITE = 3        # pages opened in a real browser when plain download shows no times
PAGE_WORDS = re.compile(r"prayer|salah|salat|namaz|jama'?a?h?t|iqam|timetable|time-table|times", re.I)
NOT_PAGES = re.compile(r"donat|zakat|login|account|cart|shop|wp-admin|facebook|twitter|instagram|whatsapp|mailto:|tel:|#", re.I)

RECIPE_PROMPT = """You are helping a mosque app read the published prayer times of {name} ({where}) automatically.
If the page shows several mosques (often named by street), map ONLY this mosque — match it by name OR by its street/address.
Below is an outline of one page of the mosque's website{what}. Today is {today}.

We need the JAMAAT times (also called iqamah / congregation / jama'ah) — NOT the start / begins / adhan times.

Reply with ONLY one JSON object, one of:

1. A dated timetable table (one row per date):
{{"kind": "table", "table": "T0", "header_rows": 1, "date_col": 0,
  "date_style": "day_of_month" | "full_date",
  "cols": {{"fajr": {{"col": 3, "nth": 0}}, "dhuhr": {{...}}, "asr": {{...}}, "maghrib": {{...}}, "isha": {{...}}, "jumuah": {{...}} or null}}}}
   - "col" is the 0-based column number as shown in the outline; "nth" is which time inside that cell (0 = first, 1 = second) when a cell holds two times.
   - date_style "day_of_month" when the date column only has a day number (1, 2, 3 … or "Mon 1"); "full_date" when it has day and month.
   - Use null for a prayer with no jamaat column (e.g. Maghrib "at sunset").

2. A box showing only today's times (elements marked E…):
{{"kind": "fields", "fields": {{"fajr": {{"el": "E4", "nth": 0}}, "dhuhr": …, "asr": …, "maghrib": …, "isha": …, "jumuah": {{"el": "E20"}} or null}}}}
   - Pick the element holding the JAMAAT time for each prayer; "nth" as above when the element holds more than one time.

3. The jamaat times are not on this page but one of its links (L…) is the timetable:
{{"kind": "follow", "link": "L3"}}

4. This page has jamaat times but in a layout that can't be mapped as above:
{{"kind": "page_ai"}}

5. No jamaat times here (only start times, or no times at all):
{{"kind": "none", "reason": "short reason"}}

Never pick start/begins columns as jamaat. If you are not sure which column is jamaat, answer kind "none" with the reason.

OUTLINE:
{outline}
"""


# ──────────────────────────── outlines ────────────────────────────

def html_outline(html: bytes, base: str) -> tuple[str, dict]:
    soup = soup_of(html)
    refs, lines = {}, []
    tables = soup.find_all("table")
    for i, t in enumerate(tables):
        rows = table_rows(t)
        if not rows or not any(times_in(" ".join(r)) for r in rows):
            continue
        refs[f"T{i}"] = ("table", i)
        lines.append(f"[T{i}] table, {len(rows)} rows x {max(len(r) for r in rows)} cols")
        show = rows[:4] + (rows[-1:] if len(rows) > 4 else [])
        for k, r in enumerate(show):
            lines.append(f"   row{'' if k < 4 else ' (last)'}: " + " | ".join(f"{c}:{x[:30]}" for c, x in enumerate(r)))
    seen, n = set(), 0
    for s in soup.find_all(string=re.compile(r"\d{1,2}\s*[:.]\s*\d{2}")):
        el = s.parent
        if el is None or id(el) in seen or not times_in(el.get_text(" ")):
            continue
        seen.add(id(el))
        refs[f"E{n}"] = ("el", el)
        lines.append(f"[E{n}] \"{el.get_text(' ', strip=True)[:40]}\"   in: {context_of(el)[:160]}")
        n += 1
        if n >= 160:
            break
    k = 0
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        label = a.get_text(" ", strip=True)
        if NOT_PAGES.search(href) or not (TIMETABLE_WORDS.search(label) or TIMETABLE_WORDS.search(href)):
            continue
        refs[f"L{k}"] = ("link", href, label)
        lines.append(f"[L{k}] {label[:60]} -> {href[:140]}")
        k += 1
        if k >= 25:
            break
    return "\n".join(lines)[:24000], refs


def pdf_outline(tables: list) -> tuple[str, dict]:
    refs, lines = {}, []
    for i, rows in enumerate(tables):
        if not rows or not any(times_in(" ".join(r)) for r in rows):
            continue
        refs[f"T{i}"] = ("table", i)
        lines.append(f"[T{i}] table, {len(rows)} rows x {max(len(r) for r in rows)} cols")
        for k, r in enumerate(rows[:4] + rows[-1:]):
            lines.append("   row: " + " | ".join(f"{c}:{x[:30]}" for c, x in enumerate(r)))
    return "\n".join(lines)[:24000], refs


def table_recipe(ans: dict, refs: dict, rows_of) -> dict | None:
    ref = refs.get(str(ans.get("table")))
    if not ref or ref[0] != "table":
        return None
    idx = ref[1]
    cols = {}
    for key in (*PRAYER_KEYS, "jumuah"):
        c = (ans.get("cols") or {}).get(key)
        if isinstance(c, dict) and isinstance(c.get("col"), int):
            cols[key] = {"col": c["col"], "nth": int(c.get("nth") or 0)}
    if sum(k in cols for k in PRAYER_KEYS) < 3 or not isinstance(ans.get("date_col"), int):
        return None
    return {"index": idx, "sig": signature(rows_of(idx)), "header_rows": int(ans.get("header_rows") or 1),
            "date_col": ans["date_col"], "date_style": ans.get("date_style") or "day_of_month", "cols": cols}


def fields_recipe(ans: dict, refs: dict) -> dict | None:
    out = {}
    for key in (*PRAYER_KEYS, "jumuah"):
        f = (ans.get("fields") or {}).get(key)
        if not isinstance(f, dict):
            continue
        ref = refs.get(str(f.get("el")))
        if not ref or ref[0] != "el":
            continue
        el = ref[1]
        ctx = context_of(el)
        m = re.search(PRAYER_WORDS[key], ctx, re.I)
        out[key] = {"sel": css_path(el), "nth": int(f.get("nth") or 0), "label": m.group(0) if m else None}
    return out if sum(k in out for k in PRAYER_KEYS) >= 3 else None


# ──────────────────────────── checking a recipe ────────────────────────────

def agreement(a: dict, b: dict) -> tuple[float, int]:
    """Share of times two readings agree on (within a minute), and how many were compared."""
    same = total = 0
    for d in set(a) & set(b):
        for p in PRAYER_KEYS:
            x, y = to_minutes((a[d] or {}).get(p), p), to_minutes((b[d] or {}).get(p), p)
            if x is None or y is None:
                continue
            total += 1
            same += abs(x - y) <= 1
    return (same / total if total else 0.0), total


class Learner:
    def __init__(self, reader: Reader, today: date):
        self.reader, self.today = reader, today

    def ask_recipe(self, m: dict, outline: str, what: str = "") -> dict:
        return self.reader.ask(RECIPE_PROMPT.format(name=m["name"], where=where(m), today=self.today,
                                                    what=what, outline=outline))

    def verify(self, m: dict, days: dict, *, text=None, data=None, media_type=None) -> tuple[bool, str, dict]:
        clean, problems, ok = check_timetable(days, m["lat"], m["lng"], self.today)
        if not ok:
            return False, "recipe times failed checks: " + "; ".join(problems[:2]), {}
        ai = self.reader.read(m, self.today, text=text, data=data, media_type=media_type)
        if not ai.get("has_jamaat"):
            return False, "AI reading of the same page found no jamaat times", {}
        share, n = agreement(clean, ai.get("days", {}))
        if n < 3 or share < 0.9:
            return False, f"recipe and AI reading disagree ({share:.0%} of {n} times match)", {}
        return True, f"{len(clean)} days, {share:.0%} agreement with AI reading", clean

    # one HTML page
    def try_page(self, m: dict, url: str, body: bytes) -> tuple[dict | None, str, dict, list]:
        """Returns (recipe or None, message, clean_days, follow_urls)."""
        slug = mawaqit_slug(body)
        if slug:
            r, msg, days = self.try_mawaqit(m, slug)
            if r:
                return r, msg, days, []
        text, files = page_text(body, url)
        outline, refs = html_outline(body, url)
        if not has_times(text):
            return None, "no times on page", {}, files
        ans = self.ask_recipe(m, outline)
        kind = ans.get("kind")
        if kind == "table":
            spec = table_recipe(ans, refs, lambda i: table_rows(soup_of(body).find_all("table")[i]))
            if spec:
                recipe = {"kind": "table", "table": spec}
                try:
                    days = apply_table_html(recipe, body, self.today)
                    ok, msg, clean = self.verify(m, days, text=text)
                    if ok:
                        return recipe, msg, clean, []
                    return None, msg, {}, files
                except RecipeError as e:
                    return None, str(e), {}, files
        if kind == "fields":
            spec = fields_recipe(ans, refs)
            if spec:
                recipe = {"kind": "fields", "fields": spec}
                try:
                    days = apply_fields(recipe, body, self.today)
                    ok, msg, clean = self.verify(m, days, text=text)
                    if ok:
                        return recipe, msg, clean, []
                    return None, msg, {}, files
                except RecipeError as e:
                    return None, str(e), {}, files
        if kind == "follow":
            ref = refs.get(str(ans.get("link")))
            return None, "timetable is on a linked page", {}, ([ref[1]] if ref and ref[0] == "link" else []) + files
        if kind == "page_ai":
            ai = self.reader.read(m, self.today, text=text)
            clean, problems, ok = check_timetable(ai.get("days", {}), m["lat"], m["lng"], self.today)
            if ai.get("has_jamaat") and ok:
                return {"kind": "page_ai"}, f"AI reads this page ({len(clean)} days)", clean, []
            return None, "page times could not be read reliably", {}, files
        return None, ans.get("reason") or "no jamaat times on page", {}, files

    def try_mawaqit(self, m: dict, slug: str) -> tuple[dict | None, str, dict]:
        url = f"https://mawaqit.net/en/{slug}"
        try:
            body, _, _ = fetch.get(url)
            days = apply_mawaqit(body, self.today)
        except (fetch.Blocked, fetch.FetchError, RecipeError) as e:
            return None, f"Mawaqit: {e}", {}
        clean, problems, ok = check_timetable(days, m["lat"], m["lng"], self.today)
        if not ok:
            return None, "Mawaqit times failed checks: " + "; ".join(problems[:2]), {}
        return {"kind": "mawaqit", "slug": slug, "url": url}, f"Mawaqit screen, {len(clean)} days", clean

    def try_file(self, m: dict, page_url: str | None, href: str, label: str) -> tuple[dict | None, str, dict]:
        try:
            data, ctype, _ = fetch.get(href)
        except (fetch.Blocked, fetch.FetchError) as e:
            return None, f"file: {e}", {}
        mt = media_type_for(href, ctype)
        if not mt:
            return None, "link is not a PDF or image", {}
        base = {"link_from": page_url, "words": link_words(href, label), "file": href if not page_url else None}
        if mt == "application/pdf":
            try:
                tables, _ = pdf_tables(data)
            except Exception:
                tables = []
            outline, refs = pdf_outline(tables)
            if refs:
                ans = self.ask_recipe(m, outline, " (tables extracted from its timetable PDF)")
                if ans.get("kind") == "table":
                    spec = table_recipe(ans, refs, lambda i: tables[i])
                    if spec:
                        recipe = {"kind": "table", "source": "pdf", "table": spec, **base}
                        try:
                            days = apply_table_pdf(recipe, data, self.today, label + " " + href)
                            ok, msg, clean = self.verify(m, days, data=data, media_type=mt)
                            if ok:
                                return recipe, "PDF table: " + msg, clean
                        except RecipeError:
                            pass
        ai = self.reader.read(m, self.today, data=data, media_type=mt)
        clean, problems, ok = check_timetable(ai.get("days", {}), m["lat"], m["lng"], self.today)
        if ai.get("has_jamaat") and ok:
            return {"kind": "file_ai", **base}, f"AI reads the {'PDF' if mt.endswith('pdf') else 'image'} ({len(clean)} days)", clean
        return None, "timetable file has no readable jamaat times", {}

    def learn(self, m: dict, start: str) -> tuple[dict, str, dict, str]:
        """Returns (recipe, message, clean_days, timetable_url)."""
        site = urlparse(start).netloc.lower().removeprefix("www.")
        queue, seen, notes = [start], set(), []
        pages = renders = 0
        while queue and pages < MAX_PAGES_PER_SITE:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                body, ctype, _ = fetch.get(url)
            except fetch.Blocked as e:
                if url == start:
                    raise
                notes.append(f"{url}: blocked")
                continue
            except fetch.FetchError as e:
                notes.append(f"{url}: {e}")
                continue
            pages += 1
            if media_type_for(url, ctype):
                recipe, msg, days = self.try_file(m, None, url, "")
                if recipe:
                    return recipe, msg, days, url
                notes.append(msg)
                continue
            if "html" not in ctype:
                continue
            recipe, msg, days, more = self.try_page(m, url, body)
            if recipe:
                return recipe, msg, days, recipe.get("url") or url
            if msg == "no times on page" and renders < MAX_RENDERS_PER_SITE and (url == start or PAGE_WORDS.search(url)):
                # Many mosque sites fill in the times with JavaScript: open the page in a browser and look again.
                renders += 1
                try:
                    rbody = fetch.render(url)
                except (fetch.Blocked, fetch.FetchError) as e:
                    notes.append(f"{urlparse(url).path or '/'}: browser — {e}")
                else:
                    recipe, msg, days, more2 = self.try_page(m, url, rbody)
                    if recipe:
                        if recipe["kind"] in ("table", "fields", "page_ai"):
                            recipe["render"] = True
                        return recipe, msg + " (page opened in a browser)", days, recipe.get("url") or url
                    more = list(dict.fromkeys(more + more2))
                    body = rbody                # look for timetable links on the rendered page too
                    msg = f"{msg} (also checked in a browser)"
            notes.append(f"{urlparse(url).path or '/'}: {msg}")
            # timetable files linked from this page
            for href in more:
                if TIMETABLE_FILE_RX.search(href):
                    label = next((a.get_text(" ", strip=True) for a in soup_of(body).find_all("a", href=True)
                                  if urljoin(url, a["href"]) == href), "")
                    recipe, msg2, days = self.try_file(m, url, href, label)
                    if recipe:
                        return recipe, msg2, days, href if recipe.get("file") else url
                    notes.append(msg2)
                elif href not in seen:
                    queue.insert(0, href)
            # other prayer-times pages on the same site, best first
            if url == start or pages <= 2:
                for link in candidate_pages(body, url, site):
                    if link not in seen and link not in queue:
                        queue.append(link)
        return {"kind": "none"}, "; ".join(notes[-4:])[:300] or "no timetable found", {}, start


TIMETABLE_FILE_RX = re.compile(r"\.(pdf|png|jpe?g|webp)(\?|$)", re.I)


def candidate_pages(body: bytes, base: str, site: str) -> list[str]:
    soup = soup_of(body)
    scored = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        if NOT_PAGES.search(href) or TIMETABLE_FILE_RX.search(href):
            continue
        if urlparse(href).netloc.lower().removeprefix("www.") != site:
            continue
        hay = a.get_text(" ", strip=True) + " " + urlparse(href).path
        if not PAGE_WORDS.search(hay):
            continue
        score = sum(bool(re.search(w, hay, re.I)) for w in (r"prayer", r"timetable", r"jama", r"salah|salat|namaz", r"iqam", r"times"))
        scored.append((score, href))
    out = []
    for _, h in sorted(scored, key=lambda x: -x[0]):
        if h not in out:
            out.append(h)
    return out[:5]


def due(store, limit: int) -> list[dict]:
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = store._req("GET", "sources?select=*,mosques(id,name,address,town,postcode,lat,lng,website)"
                             "&enabled=is.true&blocked_reason=is.null"
                             f"&or=(recipe_status.is.null,recipe_status.eq.broken,and(recipe_status.eq.none,recipe_learned_at.lt.{month_ago}))"
                             "&order=recipe_status.asc.nullsfirst,id", headers={"Range": f"0-{limit - 1}"})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=120, help="websites to learn this run")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--try", dest="try_url", metavar="URL",
                    help="learn one website and print the result, without touching the database")
    args = ap.parse_args()
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    if args.try_url:
        import json
        lat, lng = 52.48, -1.89          # checks use central England unless the mosque is in the database
        m = {"id": 0, "name": urlparse(args.try_url).netloc, "town": "UK", "lat": lat, "lng": lng}
        reader = Reader(max_calls=12)
        try:
            recipe, msg, days, url = Learner(reader, today).learn(m, args.try_url)
        except fetch.Blocked as e:
            print(json.dumps({"timetable_url": args.try_url, "recipe": None,
                              "message": f"website refuses automated visits — {e}", "ai_calls": reader.calls}, indent=2))
            return
        print(json.dumps({"timetable_url": url, "recipe": recipe, "message": msg, "ai_calls": reader.calls,
                          "first_days": dict(list(days.items())[:3])}, indent=2))
        return
    from .store import Store
    store = Store()
    reader = Reader(max_calls=config.MAX_AI_CALLS_LEARN)
    learner = Learner(reader, today)
    log, counts = [], {}

    def one(src):
        m = src["mosques"]
        now = datetime.now(timezone.utc).isoformat()
        try:
            recipe, msg, days, tt_url = learner.learn(m, src["url"])
        except fetch.Blocked as e:
            store.update_source(src["id"], {"blocked_reason": str(e), "last_status": "blocked", "last_checked": now})
            return "blocked", f"{m['name']}: blocked — {e}"
        except Exception as e:
            if "AI call limit" in str(e):
                return "stopped", f"{m['name']}: not learned (AI limit for this run reached)"
            store.update_source(src["id"], {"recipe_status": "none", "recipe_learned_at": now, "last_message": str(e)[:300]})
            return "error", f"{m['name']}: error — {str(e)[:120]}"
        breaks = int((src.get("recipe") or {}).get("breaks") or 0)
        if breaks >= 2 and recipe["kind"] in ("table", "fields"):
            # this site's layout keeps shifting: let AI read it whenever it changes instead
            recipe = {"kind": "page_ai", "render": bool(recipe.get("render")), "breaks": breaks}
            msg += " — layout changes often, so AI reads it when it changes"
        status = "none" if recipe["kind"] == "none" else "ok"
        fields = {"recipe": recipe, "recipe_status": status, "recipe_learned_at": now, "last_message": msg[:300],
                  "last_hash": None}
        if tt_url != src["url"]:
            fields["url"] = tt_url
        try:
            store.update_source(src["id"], fields)
        except RuntimeError as e:                 # this timetable URL is already another source of the mosque
            if "duplicate" in str(e) or "23505" in str(e):
                fields.pop("url")
                store.update_source(src["id"], {**fields, "enabled": False, "last_message": "duplicate of another source"})
                return "duplicate", f"{m['name']}: same timetable as another source"
            raise
        if days:
            store.save_days(m["id"], src["id"], tt_url, days)
        return recipe["kind"], f"{m['name']}: {recipe['kind']} — {msg}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(one, s) for s in due(store, args.limit)]):
            kind, line = fut.result()
            counts[kind] = counts.get(kind, 0) + 1
            log.append(line)
    summary = (f"## Recipe learning — {today}\n\n" + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
               + f" · AI calls: {reader.calls}\n\n" + "\n".join(f"- {l}" for l in sorted(log)[:600]))
    print(summary)
    import os
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary + "\n")


if __name__ == "__main__":
    main()
