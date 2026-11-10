"""Run with:  python -m pytest -q"""
from datetime import date

from collector.extract import fingerprint_text, has_times, page_text, parse_reply
from collector.run import drop_empty_days, expand_pattern
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


def test_relearn_miss_more_patient_in_first_week_of_month():
    from collector.learn import keep_working_recipe
    old = {"kind": "fields", "fields": {"fajr": {"sel": "x"}}}
    none = {"kind": "none"}
    r = old
    for n in range(1, 7):
        r, st, _ = keep_working_recipe(r, none, "no times on page", today=date(2026, 11, 2))
        assert st == "broken" and r["relearn_misses"] == n
    r, st, _ = keep_working_recipe(r, none, "no times on page", today=date(2026, 11, 2))
    assert st == "none"
    # later in the month the usual limit applies
    r, st, _ = keep_working_recipe({**old, "relearn_misses": 2}, none, "x", today=date(2026, 11, 15))
    assert st == "none"


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


def test_jumuah_only_friday_does_not_sink_todays_page_read():
    # Bournemouth / Chatham Hill style: today's times plus a "next Friday" box with only Jumu'ah.
    days = {"2026-10-24": {"fajr": "6:45", "dhuhr": "1:20", "asr": "4:45", "maghrib": "5:59", "isha": "8:15"},
            "2026-10-30": {"fajr": None, "dhuhr": None, "asr": None, "maghrib": None, "isha": None, "jumuah": "1:30"}}
    _, _, accepted = check_timetable(days, 50.74266, -1.87662, date(2026, 10, 24))
    assert not accepted                      # the safety check itself is unchanged
    clean, problems, accepted = check_timetable(drop_empty_days(days), 50.74266, -1.87662, date(2026, 10, 24))
    assert accepted and list(clean) == ["2026-10-24"] and not problems
    # A day with even one time is still counted (and still fails if it is wrong).
    assert "2026-10-25" in drop_empty_days({"2026-10-25": {"isha": "8:00"}})
    assert drop_empty_days({"2026-10-30": {"fajr": "--", "isha": "", "jumuah": "1:30"}}) == {}


def test_jumuah_from_timetable_cell():
    from collector.profile import jumuah_from_cell
    slot = lambda t: jumuah_from_cell(t)["slots"][0]["jamaat"]
    assert slot("13:15") == "13:15"
    assert slot("1:00 PM") == "13:00" and slot("1:30pm") == "13:30" and slot("1:00") == "13:00"
    assert slot("Time: 12:30 PM") == "12:30" and slot("12:00pm (Brothers only)") == "12:00"
    # ambiguous cells are not used: two times could be khutbah + jamaat or two Jumu'ahs
    assert jumuah_from_cell("12:30, 13:15") is None
    assert jumuah_from_cell("Bayaan 12:40, Azaan 13:00, Khutbah 13:10") is None
    assert jumuah_from_cell("1st 1:30 -  2nd 2:10") is None
    assert jumuah_from_cell("Iqamah: 1:30 PM & 2:30 PM") is None
    assert jumuah_from_cell("") is None and jumuah_from_cell(None) is None
    assert jumuah_from_cell("09:30") is None and jumuah_from_cell("16:45") is None
    # Mawaqit lists separate Jumu'ahs
    j = jumuah_from_cell("12:45, 01:30", separate_slots=True)
    assert [s["jamaat"] for s in j["slots"]] == ["12:45", "13:30"] and j["slots"][0]["name"] == "1st Jumu'ah"
    assert jumuah_from_cell("12:30, 17:00", separate_slots=True) is None


def test_year_long_timetable_is_cut_to_this_month():
    from datetime import date
    from collector.extract import focus_month
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
    tabs = "\n".join(months) + "\n"
    body = "".join(f"{m}\nDay | Fajr | Zuhr\n" + "".join(f"{d} | 06:{d:02d} AM | 07:00 AM | 12:10 PM | 01:00 PM | 02:15 PM | 02:45 PM\n" for d in range(1, 31))
                   for m in months)
    text = "Prayer Timetable – Test Masjid\n" + tabs + body
    out = focus_month(text, date(2026, 11, 5))
    assert "November\nDay" in out and "October\nDay" not in out and "December\nDay" not in out
    assert "Test Masjid" in out
    late = focus_month(text, date(2026, 11, 26))
    assert "November\nDay" in late and "December\nDay" in late
    assert focus_month("short page Fajr 05:00", date(2026, 11, 5)) == "short page Fajr 05:00"
    no_heads = "x" * 20000
    assert focus_month(no_heads, date(2026, 11, 5)) == no_heads


def test_prayer_screen_with_hours_and_minutes_in_separate_boxes():
    from collector.extract import page_text, has_times
    boxes = "".join(f"<div>{p}</div><div>{h}</div><div>{m}</div><div>Iqamah</div><div>{ih}</div><div>{im}</div>"
                    for p, h, m, ih, im in [("Fajr", 5, 22, 5, 45), ("Dhuhr", 11, 44, 12, 15), ("Asr", 13, 52, 14, 15),
                                            ("Maghrib", 16, 20, 16, 25), ("Isha", 17, 51, 19, 30)])
    html = ("<html><body><div>is in</div><div>01</div><div>Hours</div>" + boxes + "</body></html>").encode()
    text, _ = page_text(html, "https://masjidbox.com/prayer-times/x")
    assert "Fajr\n5:22\nIqamah\n5:45" in text and "Isha\n17:51\nIqamah\n19:30" in text and has_times(text)
    assert "01\nHours" in text
    # a page with one stray pair of numbers is left alone
    assert page_text(b"<html><body><div>Room</div><div>5</div><div>22</div></body></html>", "https://m.org/")[0] == "Room\n5\n22"


def test_jumuah_minutes_accepts_am_pm_suffix():
    from collector.profile import clean_jumuah, jumuah_minutes
    assert jumuah_minutes("13:15") == 13 * 60 + 15
    assert jumuah_minutes("13:15pm") == 13 * 60 + 15          # 'Starts at 13:15pm' (Muslim Welfare House)
    assert jumuah_minutes("1:15 pm") == 13 * 60 + 15
    assert jumuah_minutes("1.30 p.m.") == 13 * 60 + 30
    assert jumuah_minutes("12:30PM") == 12 * 60 + 30
    assert jumuah_minutes("11:45 am") == 11 * 60 + 45
    assert jumuah_minutes("1:15 am") is None
    assert jumuah_minutes("6:30") is None                      # 18:30 is no Jumu'ah time
    j = clean_jumuah({"slots": [{"name": "Friday Khutbah", "khutbah": "13:15pm", "jamaat": None}]})
    assert j["slots"][0]["khutbah"] == "13:15"


def test_prepare_media_fixes_wrong_labels_and_huge_pictures():
    import io
    import pytest
    from PIL import Image
    from collector.extract import MediaError, prepare_media, sniff_media_type

    def pic(fmt, size=(40, 30)):
        b = io.BytesIO()
        Image.new("RGB", size, "white").save(b, fmt)
        return b.getvalue()

    webp = pic("WEBP")
    assert sniff_media_type(webp) == "image/webp"
    assert prepare_media(webp, "image/jpeg") == (webp, "image/webp")       # site labelled a WebP as .jpg
    png = pic("PNG")
    assert prepare_media(png, "image/webp")[1] == "image/png"
    data, mt = prepare_media(pic("PNG", (9000, 100)), "image/png")           # over the API's 8000 px limit
    assert mt == "image/png" and max(Image.open(io.BytesIO(data)).size) <= 8000
    bmp = pic("BMP")
    data, mt = prepare_media(bmp, "image/jpeg")                              # other formats become PNG
    assert mt == "image/png" and sniff_media_type(data) == "image/png"
    assert prepare_media(b"\n\n%PDF-1.4 rest", "application/pdf") == (b"%PDF-1.4 rest", "application/pdf")
    with pytest.raises(MediaError):
        prepare_media(b"<html>not found</html>", "application/pdf")
    with pytest.raises(MediaError):
        prepare_media(b"<html>not found</html>", "image/jpeg")
