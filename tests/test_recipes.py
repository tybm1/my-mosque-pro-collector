"""Recipes and learning, tested on saved pages with a stand-in for Claude and the web.
Run with:  python -m pytest -q"""
import json
from datetime import date
from pathlib import Path

import pytest

from collector import fetch, learn, recipes
from collector.recipes import (RecipeError, apply_fields, apply_mawaqit, apply_table_html, parse_date,
                               pick_file_link, soup_of)
from collector.validate import check_timetable

FIX = Path(__file__).parent / "fixtures"
TABLE = (FIX / "table_page.html").read_bytes()
BOX = (FIX / "today_box.html").read_bytes()
TODAY = date(2026, 10, 16)
LONDON = {"id": 1, "name": "Test Masjid", "town": "London", "lat": 51.5185, "lng": -0.0659}

TABLE_ANSWER = {"kind": "table", "table": "T0", "header_rows": 2, "date_col": 0, "date_style": "day_of_month",
                "cols": {"fajr": {"col": 3}, "dhuhr": {"col": 6}, "asr": {"col": 8}, "maghrib": {"col": 9},
                         "isha": {"col": 11}, "jumuah": None}}


def table_recipe():
    outline, refs = learn.html_outline(TABLE, "https://m.org/prayer")
    spec = learn.table_recipe(TABLE_ANSWER, refs, lambda i: recipes.table_rows(soup_of(TABLE).find_all("table")[i]))
    return {"kind": "table", "table": spec}


def test_outline_shows_table_with_column_numbers():
    outline, refs = learn.html_outline(TABLE, "https://m.org/prayer")
    assert "[T0]" in outline and "3:6:15" in outline         # col 3 holds Fajr jamaat (colspan expanded)
    assert any(r[0] == "link" and r[1].endswith(".pdf") for r in refs.values())


def test_table_recipe_reads_jamaat_columns_not_start():
    days = apply_table_html(table_recipe(), TABLE, TODAY)
    assert days["2026-10-16"] == {"fajr": "6:15", "dhuhr": "1:30", "asr": "4:00", "maghrib": "6:05", "isha": "7:45"}
    clean, problems, ok = check_timetable(days, LONDON["lat"], LONDON["lng"], TODAY)
    assert ok and clean["2026-10-16"]["dhuhr"] == "13:30" and clean["2026-10-31"]["isha"] == "18:30"


def test_table_recipe_follows_table_when_page_adds_another():
    extra = TABLE.replace(b"<h2>", b"<table><tr><th>Event</th><th>Time</th></tr><tr><td>Talk</td><td>7:00</td></tr></table><h2>")
    days = apply_table_html(table_recipe(), extra, TODAY)
    assert days["2026-10-17"]["fajr"] == "6:15"


def test_table_recipe_breaks_loudly_when_table_gone():
    with pytest.raises(RecipeError):
        apply_table_html(table_recipe(), b"<html><body><p>New website coming soon</p></body></html>", TODAY)


def test_month_rollover_in_day_of_month_table():
    rows = [["Date", "Fajr"], ["30", "5:00"], ["31", "5:01"], ["1", "5:02"]]
    days = recipes.rows_to_days(rows, {"date_col": 0, "header_rows": 1, "cols": {"fajr": {"col": 1}}}, (2026, 10), TODAY)
    assert list(days) == ["2026-10-30", "2026-10-31", "2026-11-01"]


def fields_recipe():
    outline, refs = learn.html_outline(BOX, "https://m.org/")
    jam = {}
    for ref, v in refs.items():
        if v[0] == "el" and "j" in (v[1].get("class") or []):
            jam[v[1].find_previous_sibling("span").find_previous_sibling("span").get_text().lower()] = ref
    ans = {"kind": "fields", "fields": {p: {"el": jam[p]} for p in ("fajr", "dhuhr", "asr", "maghrib", "isha")}}
    return {"kind": "fields", "fields": learn.fields_recipe(ans, refs)}


def test_fields_recipe_reads_todays_box():
    r = fields_recipe()
    assert r["fields"]["fajr"]["label"].lower() == "fajr"
    days = apply_fields(r, BOX, TODAY)
    assert days == {"2026-10-16": {"fajr": "6:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "18:10", "isha": "19:45"}}


def test_fields_recipe_detects_layout_change():
    swapped = BOX.replace(b"<span>Fajr</span>", b"<span>Sunrise</span>")
    with pytest.raises(RecipeError):
        apply_fields(fields_recipe(), swapped, TODAY)


def test_mawaqit_offsets_and_fixed_times():
    cal = [{} for _ in range(12)]
    iq = [{} for _ in range(12)]
    for d in range(1, 32):
        cal[9][str(d)] = ["05:40", "07:25", "12:44", "15:33", "18:05", "19:29"]
        iq[9][str(d)] = ["06:15", "+30", "+20", "+5", "19:45"]
    conf = {"calendar": cal, "iqamaCalendar": iq, "jumua": "13:15", "jumua2": "14:00", "iqamaEnabled": True}
    page = f"<script>var confData = {json.dumps(conf)};\nvar x=1;</script>".encode()
    days = apply_mawaqit(page, TODAY, days_ahead=3)
    assert days["2026-10-16"] == {"fajr": "6:15", "dhuhr": "13:14", "asr": "15:53", "maghrib": "18:10",
                                  "isha": "19:45", "jumuah": "13:15, 14:00"}
    assert "jumuah" not in days["2026-10-17"]


def test_mawaqit_slug_found_in_embed():
    assert recipes.mawaqit_slug(b'<iframe src="https://mawaqit.net/en/w/masjid-al-noor-leicester?showOnly5PrayerTimes=0">') == "masjid-al-noor-leicester"


def test_parse_dates():
    assert parse_date("Fri 16 Oct", "full_date", None, TODAY) == date(2026, 10, 16)
    assert parse_date("16/10/2026", "full_date", None, TODAY) == date(2026, 10, 16)
    assert parse_date("January 3rd", "full_date", None, TODAY) == date(2027, 1, 3)
    assert parse_date("Mon 2", "day_of_month", (2026, 11), TODAY) == date(2026, 11, 2)


def test_pick_file_link_prefers_this_month():
    html = b"""<a href="/up/Timetable-September-2026.pdf">September prayer timetable</a>
               <a href="/up/Timetable-October-2026.pdf">October prayer timetable</a>
               <a href="/up/annual-report.pdf">Annual report</a>"""
    words = recipes.link_words("https://m.org/up/Timetable-August-2026.pdf", "August prayer timetable")
    href, _ = pick_file_link(soup_of(html), "https://m.org/", words, TODAY)
    assert href == "https://m.org/up/Timetable-October-2026.pdf"


# ─────────── end to end: learning a site (web and Claude replaced by stand-ins) ───────────

class FakeReader:
    def __init__(self, answer, ai_days):
        self.answer, self.ai_days, self.calls = answer, ai_days, 0

    def ask(self, prompt, max_tokens=2000):
        self.calls += 1
        return self.answer

    def read(self, mosque, today, **kw):
        self.calls += 1
        return {"has_jamaat": True, "days": self.ai_days}


HOME = b'<html><body><h1>Test Masjid</h1><a href="/donate">Donate</a><a href="/prayer-times">Prayer Times</a>' + b"x" * 400 + b"</body></html>"


def fake_web(monkeypatch):
    pages = {"https://m.org/": HOME, "https://m.org/prayer-times": TABLE}

    def get(url):
        if url not in pages:
            raise fetch.FetchError("HTTP 404")
        return pages[url], "text/html", "h"
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "render", lambda url: get(url)[0])   # no real browser in tests


AI_SAYS = {"2026-10-16": {"fajr": "06:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "18:05", "isha": "19:45"},
           "2026-10-17": {"fajr": "06:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "18:03", "isha": "19:45"}}


def test_learn_finds_prayer_page_and_accepts_matching_recipe(monkeypatch):
    fake_web(monkeypatch)
    r = FakeReader(TABLE_ANSWER, AI_SAYS)
    recipe, msg, days, url = learn.Learner(r, TODAY).learn(LONDON, "https://m.org/")
    assert recipe["kind"] == "table" and url == "https://m.org/prayer-times"
    assert days["2026-10-16"]["isha"] == "19:45" and "100% agreement" in msg


def test_learn_rejects_recipe_that_disagrees_with_ai(monkeypatch):
    fake_web(monkeypatch)
    wrong = json.loads(json.dumps(TABLE_ANSWER))
    wrong["cols"]["fajr"] = {"col": 2}          # the Begins column
    wrong["cols"]["isha"] = {"col": 10}
    recipe, msg, days, url = learn.Learner(FakeReader(wrong, AI_SAYS), TODAY).learn(LONDON, "https://m.org/")
    assert recipe["kind"] == "none" and days == {}


# ─────────── the daily run following a recipe ───────────

class FakeStore:
    def __init__(self):
        self.saved, self.updates = {}, {}

    def save_days(self, mid, sid, url, days):
        self.saved.update(days)
        return len(days)

    def update_source(self, sid, fields):
        self.updates.update(fields)


def test_daily_run_uses_recipe_without_ai(monkeypatch):
    from collector import run
    fake_web(monkeypatch)
    store, reader = FakeStore(), FakeReader(None, {})
    src = {"id": 9, "url": "https://m.org/prayer-times", "recipe": table_recipe(), "recipe_status": "ok",
           "fail_count": 0, "mosques": LONDON}
    stats = run.Stats()
    line = run.process(src, TODAY, reader, store, run._Counter(stats))
    assert "recipe" in line and reader.calls == 0
    assert store.saved["2026-10-16"]["dhuhr"] == "13:30" and stats["by_recipe"] == 1


def test_kept_recipe_that_reads_again_is_marked_working(monkeypatch):
    """A recipe kept after 'no times on page' looks, that reads the site fine again, is working:
    clear the miss count so separate blips on different days don't add up and park the site."""
    from collector import run
    fake_web(monkeypatch)
    store, reader = FakeStore(), FakeReader(None, {})
    src = {"id": 9, "url": "https://m.org/prayer-times", "recipe": {**table_recipe(), "relearn_misses": 2},
           "recipe_status": "broken", "fail_count": 0, "mosques": LONDON}
    run.process(src, TODAY, reader, store, run._Counter(run.Stats()))
    assert store.updates["recipe_status"] == "ok" and "relearn_misses" not in store.updates["recipe"]
    assert store.updates["recipe"]["kind"] == "table"


def test_working_recipe_is_left_alone(monkeypatch):
    from collector import run
    fake_web(monkeypatch)
    store = FakeStore()
    src = {"id": 9, "url": "https://m.org/prayer-times", "recipe": table_recipe(), "recipe_status": "ok",
           "fail_count": 0, "mosques": LONDON}
    run.process(src, TODAY, FakeReader(None, {}), store, run._Counter(run.Stats()))
    assert "recipe" not in store.updates and "recipe_status" not in store.updates


def test_daily_run_falls_back_to_ai_when_site_changes(monkeypatch):
    from collector import run
    pages = {"https://m.org/prayer-times": b"<html><body><p>Fajr 05:39 jamaat 06:15, Dhuhr 12:44 / 13:30, Asr 15:33 / 16:00, Maghrib 18:05, Isha 19:29 / 19:45</p></body></html>"}
    monkeypatch.setattr(fetch, "get", lambda url: (pages[url], "text/html", "h"))
    store, reader = FakeStore(), FakeReader(None, AI_SAYS)
    src = {"id": 9, "url": "https://m.org/prayer-times", "recipe": table_recipe(), "recipe_status": "ok",
           "fail_count": 0, "last_hash": None, "mosques": LONDON}
    line = run.process(src, TODAY, reader, store, run._Counter(run.Stats()))
    assert reader.calls == 1 and store.updates["recipe_status"] == "broken"
    assert store.saved["2026-10-16"]["isha"] == "19:45" and "no longer fits" in line


def test_learn_opens_javascript_pages_in_a_browser(monkeypatch):
    """Plain download has no times (filled in by JavaScript); the rendered page has the table."""
    shell = b"<html><body><h1>Prayer Times</h1><div id='pt'>Loading</div><script>/*fills table*/</script></body></html>"
    pages = {"https://m.org/": HOME, "https://m.org/prayer-times": shell}
    monkeypatch.setattr(fetch, "get", lambda url: (pages[url], "text/html", "h"))
    monkeypatch.setattr(fetch, "render", lambda url: TABLE if url.endswith("prayer-times") else HOME)
    recipe, msg, days, url = learn.Learner(FakeReader(TABLE_ANSWER, AI_SAYS), TODAY).learn(LONDON, "https://m.org/")
    assert recipe["kind"] == "table" and recipe["render"] is True and "browser" in msg


def test_daily_run_renders_when_recipe_says_so(monkeypatch):
    from collector import run
    monkeypatch.setattr(fetch, "get", lambda url: (b"<html><body>Loading</body></html>", "text/html", "h"))
    monkeypatch.setattr(fetch, "render", lambda url: TABLE)
    src = {"id": 9, "url": "https://m.org/prayer-times", "recipe": {**table_recipe(), "render": True},
           "recipe_status": "ok", "fail_count": 0, "mosques": LONDON}
    days, used = run.recipe_read(src, TODAY)
    assert days["2026-10-16"]["fajr"] == "6:15"


# ─────────── mosque profiles (Jumu'ah, sisters, facilities) ───────────

def test_jumuah_times_cleaned_and_checked():
    from collector.profile import clean_jumuah
    j = clean_jumuah({"slots": [{"name": "1st", "khutbah": "1:15", "jamaat": "1:30"},
                                {"name": "2nd", "khutbah": None, "jamaat": "14:30"},
                                {"name": "bad", "jamaat": "19:45"}], "note": "Arabic & English"})
    assert j["slots"] == [{"name": "1st", "khutbah": "13:15", "jamaat": "13:30"},
                          {"name": "2nd", "khutbah": None, "jamaat": "14:30"}]
    assert clean_jumuah({"slots": [{"jamaat": "20:00"}]}) is None and clean_jumuah(None) is None


def test_relevant_keeps_only_profile_lines():
    from collector.profile import relevant
    text = "Welcome\nOur history\nDonate now\nJumu'ah prayers\n1st khutbah 1:15pm\nSisters' prayer hall upstairs\nCopyright"
    out = relevant(text)
    assert "1st khutbah 1:15pm" in out and "Sisters' prayer hall upstairs" in out


# ─────────── listing check (Google Places), with Google replaced by a stand-in ───────────

def _place(pid, name, addr, lat, lng, status="OPERATIONAL", types=("mosque",)):
    return {"id": pid, "displayName": {"text": name}, "formattedAddress": addr, "businessStatus": status,
            "types": list(types), "location": {"latitude": lat, "longitude": lng}}


M = {"id": 1, "name": "Green Lane Masjid", "town": "Birmingham", "postcode": "B9 5DB", "lat": 52.4720, "lng": -1.8640}


def test_verify_open_closed_renamed_missing(monkeypatch):
    from collector import verify
    replies = []
    monkeypatch.setattr(verify, "search", lambda body: replies.pop(0))
    replies[:] = [[_place("a", "Green Lane Masjid", "20 Green Ln, Birmingham B9 5DB, UK", 52.4721, -1.8641)]]
    assert verify.judge(M)[0] == "open"
    replies[:] = [[_place("b", "Green Lane Masjid", "Birmingham B9 5DB", 52.4721, -1.8641, "CLOSED_PERMANENTLY")]]
    assert verify.judge(M)[0] == "closed"
    replies[:] = [[], [_place("c", "Masjid Al-Noor", "Birmingham B9 5DA", 52.4722, -1.8642)]]
    assert verify.judge(M)[0] == "renamed?"
    replies[:] = [[_place("d", "Tesco", "Leeds LS1 1AA", 53.8, -1.55, types=("store",))], []]
    assert verify.judge(M) == ("not_found", None, "nothing on Google at this postcode")


def test_clock_change_window():
    from datetime import date
    from collector.profile import recent_clock_change
    assert recent_clock_change(date(2026, 10, 24)) is None
    assert recent_clock_change(date(2026, 10, 30)) == date(2026, 10, 25)
    assert recent_clock_change(date(2026, 11, 9)) is None
    assert recent_clock_change(date(2027, 3, 29)) == date(2027, 3, 28)


def test_user_note_drops_page_talk():
    from collector.profile import user_note
    assert user_note("Khutbah in English and Arabic. Times are not stated as changing with the clocks.") == "Khutbah in English and Arabic."
    assert user_note("Jamaat times not printed.") is None
    assert user_note("Summer times, which apply on 2026-10-19. The Hive has different t") is None



def test_change_date_table_carries_times_down():
    from collector.recipes import rows_to_days
    spec = {"cols": {"fajr": {"col": 7}, "dhuhr": {"col": 8}, "asr": {"col": 9}, "isha": {"col": 10}},
            "date_col": 0, "date_style": "day_of_month", "header_rows": 1}
    begin = ["5:09", "7:08", "12:56", "4:46", "6:47", "8:20"]
    rows = [["Date"]]
    for n in range(1, 13):
        jam = ["", "", "", ""]
        if n == 1:
            jam = ["6:30", "1:45", "5:00", "8:30"]
        if n == 8:
            jam = ["6:45", "\u201c", "4:45", "8:15"]
        rows.append([str(n)] + begin + jam)
    days = rows_to_days(rows, spec, (2026, 10), date(2026, 10, 5))
    assert days["2026-10-05"] == {"fajr": "6:30", "dhuhr": "1:45", "asr": "5:00", "isha": "8:30"}
    assert days["2026-10-08"] == {"fajr": "6:45", "dhuhr": "1:45", "asr": "4:45", "isha": "8:15"}
    assert days["2026-10-12"]["fajr"] == "6:45"


def test_daily_table_blank_row_is_not_filled():
    from collector.recipes import rows_to_days
    spec = {"cols": {"fajr": {"col": 1}, "maghrib": {"col": 2}, "isha": {"col": 3}},
            "date_col": 0, "date_style": "day_of_month", "header_rows": 1}
    rows = [["Date"]] + [[str(n), "6:00", "18:10", "19:45"] for n in range(1, 13)]
    rows[5] = ["5", "", "", ""]
    days = rows_to_days(rows, spec, (2026, 10), date(2026, 10, 5))
    assert "2026-10-05" not in days and days["2026-10-06"]["maghrib"] == "18:10"


def test_saving_never_invents_days():
    import collector.store as st
    assert not hasattr(st, "fill_change_dates")


def test_realign_shifted_pdf_rows():
    # pdfplumber pads cells; every Tuesday is shifted one column left (Masjid Bin Baz, Oct 2026)
    def row(dn, fajr, mag, shift=0):
        r = [""] * 14
        r[1], r[4 - shift], r[7 - shift], r[10 - shift] = "Mon", str(dn), fajr, mag
        return r
    rows = [["Day", "", "", "", "Date", "", "", "Fajr", "", "", "Maghrib", "", "", ""]]
    rows += [row(d, "6:00", f"6:{40 - d}") for d in (1, 2, 3, 4, 5)]
    rows += [row(6, "6:05", "6:33", shift=1)]
    rows += [row(d, "6:10", f"6:{40 - d}") for d in (7, 8)]
    spec = {"date_col": 4, "header_rows": 1, "cols": {"fajr": {"col": 7}, "maghrib": {"col": 10}}}
    days = recipes.rows_to_days(rows, spec, (2026, 10), date(2026, 10, 5))
    assert days["2026-10-06"] == {"fajr": "6:05", "maghrib": "6:33"}
    assert len(days) == 8


def test_realign_leaves_compact_tables_alone():
    # no padding: a missing cell must not slide other prayers' times into its column
    rows = [["Date", "Fajr", "Dhuhr", "Asr", "Maghrib"]]
    rows += [[str(d), "6:00", "1:00", "4:00", "6:30"] for d in range(1, 7)]
    rows += [["7", "", "1:00", "4:00", "6:30", "note"]]
    spec = {"date_col": 0, "header_rows": 1,
            "cols": {"fajr": {"col": 1}, "dhuhr": {"col": 2}, "asr": {"col": 3}, "maghrib": {"col": 4}}}
    days = recipes.rows_to_days(rows, spec, (2026, 10), date(2026, 10, 5))
    assert days["2026-10-07"]["fajr"] is None
    assert days["2026-10-07"]["dhuhr"] == "1:00"
