"""The daily collector. Run with:  python -m collector.run

For every enabled timetable source:
  1. fetch it politely (robots.txt respected — blocked sites are marked and never retried)
  2. if the site has a learned recipe (collector.learn), read the times with plain code — no AI
  3. otherwise (or if the recipe no longer fits) have Claude read the jamaat times,
     but only when the page/PDF changed since last time
  4. validate every day against sunrise/sunset for that mosque
  5. save good days to the database, with the source URL
Several websites are checked at once (COLLECTOR_WORKERS), each at most one request every 3 s.
"""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, fetch
from .extract import Reader, fingerprint_text, has_times, media_type_for, page_text
from .recipes import (RecipeError, apply_fields, apply_mawaqit, apply_table_html, apply_table_pdf,
                      pick_file_link, soup_of)
from .store import Store
from .validate import check_timetable

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def expand_pattern(pattern: str, today: date) -> list[str]:
    """Monthly-file addresses for this month and next."""
    urls = []
    for d in (today.replace(day=1), (today.replace(day=1) + timedelta(days=32)).replace(day=1)):
        urls.append(pattern.replace("{YYYY}", f"{d.year}").replace("{MM}", f"{d.month:02d}")
                    .replace("{MONTH}", MONTHS[d.month - 1]).replace("{MON}", MONTHS[d.month - 1][:3])
                    .replace("{month}", MONTHS[d.month - 1].lower()))
    return urls


def read_url(url: str, mosque: dict, today: date, reader: Reader, seen: set, render: bool = False):
    """Returns (status, fingerprint, days, message, extra_urls)."""
    if render:
        body = fetch.render(url)
        ctype, raw_hash = "text/html", ""
    else:
        body, ctype, raw_hash = fetch.get(url)
    mt = media_type_for(url, ctype)
    if mt:                                   # PDF or image
        if raw_hash in seen:
            return "unchanged", raw_hash, {}, "", []
        out = reader.read(mosque, today, data=body, media_type=mt)
        return ("ok" if out.get("has_jamaat") else "no_times"), raw_hash, out.get("days", {}), out.get("note", ""), []
    text, links = page_text(body, url)
    fp = fingerprint_text(text + "\n" + "\n".join(links))   # a new PDF link counts as a change
    if fp in seen:
        return "unchanged", fp, {}, "", links
    if not has_times(text):
        return "no_times", fp, {}, "page has no times in its text (may need a linked PDF)", links
    out = reader.read(mosque, today, text=text)
    return ("ok" if out.get("has_jamaat") else "no_times"), fp, out.get("days", {}), out.get("note", ""), links


RANK = {"ok": 4, "unchanged": 3, "no_times": 2, "invalid": 1}


def ai_read(src: dict, urls: list[str], today: date, reader: Reader, store: Store, stats: dict) -> tuple[dict, str]:
    """Claude reads the page/file — only if it changed since the last read. Returns (source fields, log note)."""
    m = src["mosques"]
    best = None
    seen = set(filter(None, (src.get("last_hash") or "").split("|")))
    fps = []
    for url in urls:
        try:
            status, fp, days, note, links = read_url(url, m, today, reader, seen,
                                                     render=bool((src.get("recipe") or {}).get("render")))
        except fetch.FetchError:
            if src.get("url_pattern"):
                continue            # next month's file may not exist yet
            raise
        fps.append(fp)
        used = url
        # A page with only start times often links the real timetable PDF: try those.
        if status == "no_times" and links:
            for link in links:
                try:
                    s2, fp2, d2, n2, _ = read_url(link, m, today, reader, seen)
                except (fetch.FetchError, fetch.Blocked):
                    continue
                fps.append(fp2)
                if s2 in ("ok", "unchanged"):
                    status, fp, days, note, used = s2, fp2, d2, n2, link
                    break
        if status == "unchanged":
            if not best or RANK[best[0]] < RANK["unchanged"]:
                best = ("unchanged", fp, {}, "")
            continue
        clean, problems, accepted = check_timetable(days, m["lat"], m["lng"], today) if status == "ok" else ({}, [], False)
        if status == "ok" and not accepted:
            status, note = "invalid", "; ".join(problems[:3]) or "no usable days"
        saved = store.save_days(m["id"], src["id"], used, clean) if clean else 0
        stats["days_saved"] += saved
        if saved:
            stats["changed"] += 1
        if not best or RANK[status] > RANK[best[0]]:
            best = (status, fp, clean, note if status != "ok" else f"{saved} days saved (AI read)")
    if best is None:
        raise fetch.FetchError("no file found for this month")
    status, fp, clean, note = best
    fields = {"last_status": status, "last_message": (note or "")[:300], "last_hash": "|".join(fps)[:2000]}
    return fields, note


def file_for(recipe: dict, today: date) -> tuple[str, str]:
    """The current timetable file of a mosque that posts a new PDF/photo each month."""
    if recipe.get("file"):
        return recipe["file"], ""
    body, _, _ = fetch.get(recipe["link_from"])
    hit = pick_file_link(soup_of(body), recipe["link_from"], recipe.get("words") or [], today)
    if not hit:
        raise RecipeError("timetable file link not found on the page any more")
    return hit


def recipe_read(src: dict, today: date) -> tuple[dict, str]:
    """Follow the learned recipe with plain code. Returns (days, url used). Raises RecipeError if it no longer fits."""
    r = src["recipe"]
    kind = r["kind"]
    if kind == "mawaqit":
        body, _, _ = fetch.get(r["url"])
        return apply_mawaqit(body, today), r["url"]
    if kind == "table" and r.get("source") == "pdf":
        href, label = file_for(r, today)
        data, _, _ = fetch.get(href)
        return apply_table_pdf(r, data, today, label + " " + href), href
    body = fetch.render(src["url"]) if r.get("render") else fetch.get(src["url"])[0]
    if kind == "table":
        return apply_table_html(r, body, today), src["url"]
    if kind == "fields":
        return apply_fields(r, body, today), src["url"]
    raise RecipeError(f"unknown recipe kind {kind}")


def process(src: dict, today: date, reader: Reader, store: Store, stats: dict) -> str:
    m = src["mosques"]
    now = datetime.now(timezone.utc).isoformat()
    recipe = src.get("recipe") or {}
    kind = recipe.get("kind")
    try:
        if src.get("url_pattern"):
            fields, note = ai_read(src, expand_pattern(src["url_pattern"], today), today, reader, store, stats)
        elif kind in ("table", "fields", "mawaqit"):
            try:
                days, used = recipe_read(src, today)
                clean, problems, ok = check_timetable(days, m["lat"], m["lng"], today)
                if not ok:
                    raise RecipeError("recipe times failed checks: " + "; ".join(problems[:2]))
                saved = store.save_days(m["id"], src["id"], used, clean)
                stats["days_saved"] += saved
                stats["changed"] += 1
                stats["by_recipe"] += 1
                fields, note = {"last_status": "ok", "last_message": f"{saved} days (recipe)"}, f"{saved} days (recipe)"
            except RecipeError as e:
                # Site changed: read it with AI today, and learn the recipe again tomorrow.
                url = src["url"] if kind != "mawaqit" else recipe["url"]
                if recipe.get("source") == "pdf":
                    url = file_for(recipe, today)[0]
                fields, note = ai_read(src, [url], today, reader, store, stats)
                fields["recipe_status"] = "broken"
                note = f"recipe no longer fits ({e}); {note}"
        elif kind == "page_ai":
            fields, note = ai_read(src, [src["url"]], today, reader, store, stats)
        elif kind == "file_ai":
            fields, note = ai_read(src, [file_for(recipe, today)[0]], today, reader, store, stats)
        else:
            stats["waiting"] += 1
            return f"{m['name']}: waiting — {'no timetable found on the site' if kind == 'none' else 'recipe not learned yet'}"
        stats["checked"] += 1
        fields["last_checked"] = now
        st = fields["last_status"]
        if st == "ok":
            fields.update(last_changed=now, fail_count=0)
        elif st == "unchanged":
            fields["fail_count"] = 0
            stats["skipped"] += 1
        else:
            fields["fail_count"] = src["fail_count"] + 1
            if st == "invalid":
                fields["last_hash"] = None      # read it again tomorrow
        store.update_source(src["id"], fields)
        return f"{m['name']}: {st} — {note}"
    except fetch.Blocked as e:
        store.update_source(src["id"], {"last_checked": now, "last_status": "blocked", "blocked_reason": str(e)})
        stats["failed"] += 1
        return f"{m['name']}: BLOCKED — {e} (will not be fetched again)"
    except Exception as e:                # one bad source never stops the run
        fails = src["fail_count"] + 1
        fields = {"last_checked": now, "last_status": "error", "last_message": str(e)[:300], "fail_count": fails}
        if fails >= config.DISABLE_AFTER_FAILS:
            fields["enabled"] = False
        if isinstance(e, RecipeError):
            fields["recipe_status"] = "broken"
        store.update_source(src["id"], fields)
        stats["failed"] += 1
        return f"{m['name']}: error — {str(e)[:120]}"


class Stats(dict):
    """Counters shared by the worker threads."""
    def __init__(self):
        super().__init__(checked=0, changed=0, days_saved=0, failed=0, skipped=0, by_recipe=0, waiting=0)
        self._lock = threading.Lock()

    def add(self, k, n=1):
        with self._lock:
            self[k] = self.get(k, 0) + n


class _Counter:
    """Lets process() write stats["x"] += 1 safely from several threads."""
    def __init__(self, stats: Stats):
        self.s = stats

    def __getitem__(self, k):
        return 0

    def __setitem__(self, k, v):
        self.s.add(k, v)


def main():
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    store, reader = Store(), Reader()
    run_id = store.start_run()
    stats = Stats()
    log = []
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        futs = [pool.submit(process, src, today, reader, store, _Counter(stats)) for src in store.due_sources()]
        for f in as_completed(futs):
            log.append(f.result())
    stats["ai_calls"] = reader.calls
    store.prune()
    record = {k: stats[k] for k in ("checked", "changed", "days_saved", "failed", "skipped", "ai_calls")}
    store.finish_run(run_id, {**record, "notes": "\n".join(sorted(log))[-20000:]})
    summary = (f"## Jamaat collector — {today}\n\nChecked {stats['checked']} sources · "
               f"{stats['changed']} updated ({stats['by_recipe']} by recipe, no AI) · {stats['days_saved']} days saved · "
               f"{stats['skipped']} unchanged · {stats['failed']} failed · {stats['waiting']} waiting for a recipe · "
               f"{stats['ai_calls']} AI reads\n\n" + "\n".join(f"- {l}" for l in sorted(log)[:600]))
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary + "\n")


if __name__ == "__main__":
    sys.exit(main())
