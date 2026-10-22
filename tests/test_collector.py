"""Run with:  python -m pytest -q"""
from datetime import date

from collector.extract import fingerprint_text, has_times, page_text, parse_reply
from collector.run import expand_pattern
from collector.suncalc import sun_times
from collector.validate import check_day, check_timetable, to_minutes

LONDON = (51.5185, -0.0659)        # East London Mosque
GLASGOW = (55.8519, -4.2490)


def test_sun_times_london_october():
    s = sun_times(date(2026, 10, 16), *LONDON)
    assert 7 * 60 + 20 <= s["sunrise"] <= 7 * 60 + 30          # ~07:26 BST
    assert 18 * 60 <= s["sunset"] <= 18 * 60 + 10              # ~18:04 BST
    s = sun_times(date(2026, 12, 21), *LONDON)
    assert 15 * 60 + 50 <= s["sunset"] <= 15 * 60 + 58         # ~15:53 GMT


def test_twelve_hour_times_fixed():
    assert to_minutes("1:30", "dhuhr") == 13 * 60 + 30
    assert to_minutes("12:45", "dhuhr") == 12 * 60 + 45
    assert to_minutes("7.45", "isha") == 19 * 60 + 45
    assert to_minutes("6:15", "fajr") == 6 * 60 + 15
    assert to_minutes("25:00", "fajr") is None


def test_real_finsbury_park_day_passes():
    row = {"fajr": "06:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "18:15", "isha": "19:45"}
    ok, why = check_day(date(2026, 10, 16), row, 51.5642, -0.1067)
    assert why is None and ok["isha"] == "19:45"


def test_start_times_column_rejected():
    # Someone read Asr start (Shafi) for Maghrib etc. — out of order / wrong for sunset.
    bad = {"fajr": "05:35", "dhuhr": "12:47", "asr": "18:05", "maghrib": "15:30", "isha": "19:49"}
    ok, why = check_day(date(2026, 10, 16), bad, *LONDON)
    assert ok is None


def test_maghrib_far_from_sunset_rejected():
    row = {"fajr": "06:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "19:30", "isha": "20:45"}
    ok, why = check_day(date(2026, 10, 16), row, *LONDON)
    assert ok is None and "Maghrib" in why


def test_glasgow_summer_late_isha_ok():
    row = {"fajr": "03:00", "dhuhr": "13:30", "asr": "18:00", "maghrib": "22:10", "isha": "23:30"}
    ok, why = check_day(date(2026, 6, 20), row, *GLASGOW)
    assert why is None, why


def test_whole_timetable_rejected_when_mostly_wrong():
    days = {f"2026-10-{d}": {"fajr": "05:35", "dhuhr": "12:47", "asr": "15:30", "maghrib": "12:00", "isha": "19:49"}
            for d in range(16, 26)}
    clean, problems, accepted = check_timetable(days, *LONDON, today=date(2026, 10, 16))
    assert not accepted and clean == {}


def test_old_and_far_future_days_ignored():
    good = {"fajr": "06:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "18:10", "isha": "19:45"}
    days = {"2026-01-05": good, "2026-10-16": good, "2027-06-01": good}
    clean, _, accepted = check_timetable(days, *LONDON, today=date(2026, 10, 16))
    assert accepted and list(clean) == ["2026-10-16"]


def test_jumuah_only_kept_on_fridays():
    row = {"fajr": "06:15", "dhuhr": "13:30", "asr": "16:00", "maghrib": "18:10", "isha": "19:45", "jumuah": "13:15"}
    fri, _ = check_day(date(2026, 10, 16), row, *LONDON)
    sat, _ = check_day(date(2026, 10, 17), {**row, "maghrib": "18:08"}, *LONDON)
    assert fri["jumuah"] == "13:15" and sat["jumuah"] is None


def test_page_text_keeps_table_rows_and_finds_pdf():
    html = b"""<html><body><script>var x=1</script>
      <table><tr><th>Date</th><th>Fajr</th><th>Iqamah</th></tr><tr><td>16</td><td>05:35</td><td>06:15</td></tr></table>
      <a href="/files/Oct-2026-Timetable.pdf">Download prayer timetable</a>
      <a href="/about.pdf">About us</a></body></html>"""
    text, links = page_text(html, "https://example.org/prayer")
    assert "16 | 05:35 | 06:15" in text and "var x" not in text
    assert links == ["https://example.org/files/Oct-2026-Timetable.pdf"]


def test_fingerprint_ignores_live_clock():
    assert fingerprint_text("Now 13:20:05 Fajr 06:15") == fingerprint_text("Now 13:20:41 Fajr 06:15")
    assert fingerprint_text("Fajr 06:15") != fingerprint_text("Fajr 06:30")


def test_has_times():
    assert has_times("Fajr 06:15 Dhuhr 13:30 Asr 16:00 Maghrib 18:15 Isha 19:45")
    assert not has_times("Welcome to our mosque")


def test_parse_reply_tolerates_prose():
    out = parse_reply('Here you go:\n{"has_jamaat": true, "days": {"2026-10-16": {"fajr": "06:15"}}}\nThanks')
    assert out["has_jamaat"] and out["days"]["2026-10-16"]["fajr"] == "06:15"
    assert parse_reply("no json")["days"] == {}


def test_monthly_pattern():
    urls = expand_pattern("https://x.org/uploads/{YYYY}/{MM}/Timetable-{MONTH}-{YYYY}.pdf", date(2026, 12, 3))
    assert urls == ["https://x.org/uploads/2026/12/Timetable-December-2026.pdf",
                    "https://x.org/uploads/2027/01/Timetable-January-2027.pdf"]


def test_relearn_miss_keeps_working_recipe_then_gives_up():
    from collector.learn import keep_working_recipe
    old = {"kind": "fields", "fields": {"fajr": {"sel": "x"}}}
    none = {"kind": "none"}
    r, st, _ = keep_working_recipe(old, none, "no times on page")
    assert st == "broken" and r["kind"] == "fields" and r["relearn_misses"] == 1
    r, st, _ = keep_working_recipe(r, none, "no times on page")
    assert st == "broken" and r["relearn_misses"] == 2
    r, st, _ = keep_working_recipe(r, none, "no times on page")
    assert st == "none" and r == none
    # a site that never had a recipe is marked 'none' straight away; a new recipe always wins
    assert keep_working_recipe({"kind": "none"}, none, "")[1] == "none"
    assert keep_working_recipe(None, none, "")[1] == "none"
    new = {"kind": "table", "table": {}}
    assert keep_working_recipe(old, new, "ok") == (new, "ok", "ok")


def test_relearn_miss_retried_when_source_gave_times_recently():
    from datetime import datetime, timedelta, timezone
    from collector.learn import keep_working_recipe, worked_recently
    none = {"kind": "none"}
    r, st, _ = keep_working_recipe(none, none, "no times on page", recent=True)
    assert st == "broken" and r["kind"] == "none" and r["relearn_misses"] == 1
    r, st, _ = keep_working_recipe(r, none, "", recent=True)
    r, st, _ = keep_working_recipe(r, none, "", recent=True)
    assert st == "none"
    now = datetime.now(timezone.utc)
    assert worked_recently({"last_changed": (now - timedelta(days=1)).isoformat()})
    assert not worked_recently({"last_changed": (now - timedelta(days=30)).isoformat()})
    assert not worked_recently({"last_changed": None})
