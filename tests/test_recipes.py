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


def test_pick_file_link_follows_islamic_months():
    """Timetables named by Islamic month (Rochdale: Jumad-Al-Ula.pdf, ending 10 Nov 2026) must move on to the
    next month's file; recipes learned with the old month's words still find it."""
    html = b"""<a href="/up/2026/10/Jumad-Al-Ula.pdf">Download Timetable</a>
               <a href="/up/2026/11/Jumad-Al-Akhirah.pdf">Download Timetable</a>"""
    old_words = ["download", "timetable", "jumad", "ula"]
    href, _ = pick_file_link(soup_of(html), "https://m.org/", old_words, date(2026, 11, 11))
    assert href.endswith("Jumad-Al-Akhirah.pdf")
    # mid-month the current file wins even when listed second
    html2 = html.replace(b"Akhirah", b"XXX").replace(b"Jumad-Al-Ula", b"Rabi-ul-Thani") + \
        b'<a href="/up/2026/10/Jumada-I-1448.pdf">Download Timetable</a>'
    href, _ = pick_file_link(soup_of(html2), "https://m.org/", old_words, date(2026, 10, 25))
    assert href.endswith("Jumada-I-1448.pdf")
    # only last month's file posted yet: still picked (the date checks decide what is usable)
    only_old = b'<a href="/up/2026/10/Jumad-Al-Ula.pdf">Download Timetable</a>'
    assert pick_file_link(soup_of(only_old), "https://m.org/", old_words, date(2026, 11, 11))
    assert recipes.link_words("https://m.org/up/Jumad-Al-Ula-1448.pdf", "Download") == ["download"]


def test_old_timetable_files_are_not_picked():
    """A picture uploaded years ago (e.g. a 'December 2021' screenshot still on the page) must not be
    read as this year's timetable."""
    today = date(2026, 11, 6)
    old = "https://m.org/wp-content/uploads/2021/12/Screen-Shot-2021-12-05-at-17.23.20.png"
    assert recipes.old_file_year(old, today) == 2021
    assert recipes.old_file_year("https://m.org/files/ramadaan-1442-timetable.pdf", today) == 1442
    assert recipes.old_file_year("https://m.org/uploads/2025/12/Nov26-scaled.jpg", today) is None
    assert recipes.old_file_year("https://m.org/uploads/2025/09/1/SALAH-NOVEMBER-2026.jpg", today) is None
    assert recipes.old_file_year("https://m.org/uploads/2025/10/Timetable-Nov-2025.pdf", today) == 2025
    assert recipes.old_file_year("https://m.org/Al-Ikhlas-November-2026.pdf", today) is None
    html = f'<img src="{old}" width="800" height="900" alt="Screen Shot">'.encode()
    assert pick_file_link(soup_of(html), "https://m.org/prayer-times/", ["screen", "shot"], today) is None


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


def test_learn_opens_browser_when_plain_page_shows_only_next_prayers(monkeypatch):
    """Plain page has only a 'next 5 prayers' box (1 day); the rendered page has the month table."""
    box = (b"<html><body><h1>Prayer timetable</h1><p>Loading</p><p>Next 5 Salah Times</p>"
           b"<div>Maghrib Today 6:05 PM 6:05 PM</div><div>Isha Today 7:30 PM 7:45 PM</div></body></html>")
    pages = {"https://m.org/": HOME, "https://m.org/prayer-times": box}
    monkeypatch.setattr(fetch, "get", lambda url: (pages[url], "text/html", "h"))
    monkeypatch.setattr(fetch, "render", lambda url: TABLE if url.endswith("prayer-times") else HOME)

    class Reader(FakeReader):
        def read(self, mosque, today, **kw):
            self.calls += 1
            full = "Fajr" in kw.get("text", "") and "17" in kw.get("text", "")
            return {"has_jamaat": True, "days": AI_SAYS if full else {"2026-10-16": AI_SAYS["2026-10-16"]}}

    reader = Reader({"kind": "page_ai"}, AI_SAYS)
    recipe, msg, days, url = learn.Learner(reader, TODAY).learn(LONDON, "https://m.org/")
    assert recipe["render"] is True and "browser" in msg and len(days) == 2
    assert url == "https://m.org/prayer-times"


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


# ─────────── finding the timetable on more websites ───────────

def test_learn_follows_a_site_that_moved_to_a_new_address(monkeypatch):
    """Old domain redirects to a new one: links on the new domain are the mosque's own pages."""
    home = HOME.replace(b'href="/prayer-times"', b'href="https://new-m.org/prayer-times"')
    pages = {"https://old-m.org/": home, "https://new-m.org/prayer-times": TABLE}

    def get(url):
        if url not in pages:
            raise fetch.FetchError("HTTP 404")
        if url == "https://old-m.org/":
            fetch.final_url[url] = "https://new-m.org/"
        return pages[url], "text/html", "h"
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "render", lambda url: get(url)[0])
    recipe, msg, days, url = learn.Learner(FakeReader(TABLE_ANSWER, AI_SAYS), TODAY).learn(LONDON, "https://old-m.org/")
    assert recipe["kind"] == "table" and url == "https://new-m.org/prayer-times"


def test_learn_tries_the_usual_prayer_times_address_when_menu_has_no_link(monkeypatch):
    home = b"<html><body><h1>Test Masjid</h1><a href='/about'>About</a>" + b"x" * 400 + b"</body></html>"
    pages = {"https://m.org/": home, "https://m.org/prayer-timetable/": TABLE}

    def get(url):
        if url not in pages:
            raise fetch.FetchError("HTTP 404")
        return pages[url], "text/html", "h"
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "render", lambda url: get(url)[0])
    recipe, msg, days, url = learn.Learner(FakeReader(TABLE_ANSWER, AI_SAYS), TODAY).learn(LONDON, "https://m.org/")
    assert recipe["kind"] == "table" and url == "https://m.org/prayer-timetable/"
    assert "404" not in msg


def test_refused_robots_txt_is_not_a_no(monkeypatch):
    """RFC 9309: a robots.txt that answers 403/404 has no rules; only a real Disallow blocks us."""
    class R:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text
    answers = {"https://a.org/robots.txt": R(403), "https://b.org/robots.txt": R(200, "User-agent: *\nDisallow: /"),
               "https://c.org/robots.txt": R(200, "User-agent: *\nDisallow:\n")}
    monkeypatch.setattr(fetch._session, "get", lambda url, **kw: answers[url])
    fetch._robots.clear()
    assert fetch.allowed("https://a.org/prayer-times")
    assert not fetch.allowed("https://b.org/prayer-times")
    assert fetch.allowed("https://c.org/prayer-times")


def test_learn_follows_a_timetable_service_the_mosque_links_to(monkeypatch):
    """Mosques often publish times on MasjidBox / MyMasjid instead of their own site."""
    home = b"<html><body><h1>Test Masjid</h1><a href='https://masjidbox.com/prayer-times/test'>Prayer times</a>" \
           b"<iframe src='https://time.my-masjid.com/timingscreen/abc'></iframe>" + b"x" * 400 + b"</body></html>"
    pages = {"https://m.org/": home, "https://masjidbox.com/prayer-times/test": TABLE}

    def get(url):
        if url not in pages:
            raise fetch.FetchError("HTTP 404")
        return pages[url], "text/html", "h"
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "render", lambda url: get(url)[0])
    links = learn.candidate_pages(home, "https://m.org/", "m.org")
    assert links[0] == "https://time.my-masjid.com/timingscreen/abc" and "https://masjidbox.com/prayer-times/test" in links
    recipe, msg, days, url = learn.Learner(FakeReader(TABLE_ANSWER, AI_SAYS), TODAY).learn(LONDON, "https://m.org/")
    assert recipe["kind"] == "table" and url == "https://masjidbox.com/prayer-times/test"


def test_learn_reads_a_timetable_picture_posted_on_the_prayer_page(monkeypatch):
    """Prayer page shows this month's timetable as a photo (no link to click)."""
    page = (b"<html><body><h1>Prayer Timings</h1><img src='/wp-content/uploads/logo.png' width='600' height='600'>"
            b"<img src='/wp-content/uploads/2026/10/WhatsApp-Image-2026-10-02-at-09.37.34.jpeg' width='900' height='1200'>"
            + b"x" * 400 + b"</body></html>")
    pages = {"https://m.org/": HOME.replace(b"/prayer-times", b"/prayer-timings/"), "https://m.org/prayer-timings/": page,
             "https://m.org/wp-content/uploads/2026/10/WhatsApp-Image-2026-10-02-at-09.37.34.jpeg": b"\xff\xd8jpeg"}

    def get(url):
        if url not in pages:
            raise fetch.FetchError("HTTP 404")
        return pages[url], ("image/jpeg" if url.endswith("jpeg") else "text/html"), "h"
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "render", lambda url: get(url)[0])
    recipe, msg, days, url = learn.Learner(FakeReader({"kind": "none"}, AI_SAYS), TODAY).learn(LONDON, "https://m.org/")
    assert recipe["kind"] == "file_ai" and recipe["link_from"] == "https://m.org/prayer-timings/"
    assert days["2026-10-16"]["isha"] == "19:45"
    # next month the daily run finds the new picture on the same page
    from collector.recipes import pick_file_link, soup_of
    nxt = page.replace(b"2026/10/WhatsApp-Image-2026-10-02-at-09.37.34", b"2026/11/WhatsApp-Image-2026-11-01-at-10.02.11")
    hit = pick_file_link(soup_of(nxt), "https://m.org/prayer-timings/", recipe["words"], TODAY.replace(month=11))
    assert hit and "2026-11-01" in hit[0]


def test_profile_reads_jumuah_from_masjidbox_screen_and_moved_site(monkeypatch):
    from collector import profile
    home = (b"<html><body><h1>Test Masjid</h1><p>Welcome to our new website, all are welcome at the masjid.</p>"
            b"<a href='https://masjidbox.com/prayer-times/test'>Prayer times</a>" + b"<p>about us</p>" * 60 + b"</body></html>")
    screen = (b"<html><body><div>Fajr</div><div>5</div><div>22</div><div>Iqamah</div><div>5</div><div>45</div>"
              b"<div>Dhuhr</div><div>11</div><div>44</div><div>Asr</div><div>13</div><div>52</div>"
              b"<div>Maghrib</div><div>16</div><div>20</div><div>Isha</div><div>17</div><div>51</div>"
              b"<div>Jumuah</div><div>12</div><div>30</div></body></html>")
    pages = {"https://old-m.org/": home, "https://masjidbox.com/prayer-times/test": screen}

    def get(url):
        if url == "https://old-m.org/":
            fetch.final_url[url] = "https://new-m.org/"
        return pages[url], "text/html", "h"
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "render", lambda url: get(url)[0])
    got, combined, media = profile.site_pages(LONDON, "https://old-m.org/")
    assert any(u == "https://masjidbox.com/prayer-times/test" for u, _ in got)
    assert "12:30" in combined


def test_times_with_am_pm_attached_count_as_times():
    """'5:24am' / '12:30pm' (no space) must count: such pages were parked as 'no times on page'."""
    from collector.extract import has_times
    from collector.profile import JUM
    assert has_times("Fajr 5:24am N/A Zuhr 11:48am 1:15pm Asr 2:35pm 3:00pm Maghrib 4:26pm 4:28pm")
    assert not has_times("Call 0800 123 456, 12.50p, 1.25kg, 3.5")
    assert JUM.search("Early Jummuah Next 12:15 PM") and JUM.search("Jumu’ah") and not JUM.search("jumping")


def test_friday_rows_shifted_by_extra_jumuah_cell_are_read():
    """Northwich: on Fridays the PDF row has an extra Jummah cell and every cell after the date sits
    one column right, so Fridays were dropped."""
    def row(cells):
        r = [""] * 41
        for i, v in cells.items():
            r[i] = v
        return r
    rows = [row({0: "Date", 3: "Day", 24: "Fajr", 27: "Zohar", 30: "Asr", 33: "Maghrib", 36: "Isha"})]
    for d in range(1, 15):
        if d in (6, 13):
            rows.append(row({1: str(d), 4: "Fri", 7: "5:39", 10: "7:18", 13: "11:54", 16: "2:39", 19: "4:29", 22: "6:08",
                             25: "6:30", 28: "1:00", 31: "3:00", 34: "4:29", 37: "7:00", 40: "12:45"}))
        else:
            rows.append(row({1: str(d), 3: "Sun", 6: "5:30", 9: "7:09", 12: "11:54", 15: "2:48", 18: "4:38", 21: "6:16",
                             24: "6:30", 27: "1:00", 30: "3:00", 33: "4:38", 36: "7:00"}))
    spec = {"cols": {"fajr": {"col": 24}, "dhuhr": {"col": 27}, "asr": {"col": 30}, "maghrib": {"col": 33},
                     "isha": {"col": 36}}, "date_col": 1, "date_style": "day_of_month", "header_rows": 1}
    days = recipes.rows_to_days(rows, spec, (2026, 11), date(2026, 11, 1))
    assert days["2026-11-06"] == {"fajr": "6:30", "dhuhr": "1:00", "asr": "3:00", "maghrib": "4:29", "isha": "7:00"}
    assert "2026-11-13" in days and days["2026-11-05"]["maghrib"] == "4:38"


def test_broken_gzip_is_fetched_again_uncompressed(monkeypatch):
    import requests

    class R:
        def __init__(self, broken):
            self.broken, self.status_code, self.url, self.headers = broken, 200, "https://m.org/", {"Content-Type": "text/html"}

        def iter_content(self, n):
            if self.broken:
                raise requests.exceptions.ContentDecodingError("Error -3 while decompressing data")
            yield b"<html>ok</html>"

    calls = []

    def fake_get(url, timeout=None, stream=None, headers=None):
        calls.append(headers)
        return R(broken=not headers)

    monkeypatch.setattr(fetch, "allowed", lambda url: True)
    monkeypatch.setattr(fetch, "_wait_turn", lambda url: None)
    monkeypatch.setattr(fetch._session, "get", fake_get)
    body, ctype, _ = fetch.get("https://m.org/")
    assert body == b"<html>ok</html>" and calls == [None, {"Accept-Encoding": "identity"}]


def test_learn_starts_from_homepage_when_saved_page_is_gone(monkeypatch):
    """Saved address is an old sub-page that now answers 404: look from the homepage instead."""
    fake_web(monkeypatch)
    recipe, msg, days, url = learn.Learner(FakeReader(TABLE_ANSWER, AI_SAYS), TODAY).learn(LONDON, "https://m.org/our-centre")
    assert recipe["kind"] == "table" and url == "https://m.org/prayer-times"


def test_candidate_pages_include_the_mosques_own_subdomain():
    """duhornchurch.org.uk links 'View Full Timetable' to prayertime.duhornchurch.org.uk/calendar."""
    body = (b'<html><body><a href="https://prayertime.duhornchurch.org.uk/calendar">View Full Timetable</a>'
            b'<a href="https://otherduhornchurch.org.uk/prayer-times">Prayer times</a>'
            b'<a href="https://elsewhere.org/prayer-times">Prayer times</a></body></html>')
    links = learn.candidate_pages(body, "https://duhornchurch.org.uk/", "duhornchurch.org.uk")
    assert links == ["https://prayertime.duhornchurch.org.uk/calendar"]
