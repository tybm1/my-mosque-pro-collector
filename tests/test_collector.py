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


def test_reader_prompt_ignores_tomorrow_notices():
    # Morden MICC widget: today's row + "tomorrow 19:15" under Isha → the AI made a one-time row for
    # tomorrow and the page failed the 70% check (1 good day of 2).
    from collector.extract import PROMPT
    assert "tomorrow Isha 19:15" in PROMPT and "give no row for that date" in PROMPT


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


def test_ambiguous_friday_cell_is_read_from_the_timetable_file(monkeypatch):
    """Friday cell '12:30, 13:30' from a timetable PDF/picture: the file is read once by the AI (labels say which
    is khutbah and which is jamaat); a file showing no Jumu'ah is not read again; pages are left alone."""
    from datetime import date
    import collector.profile as P
    friday = "https://x.org/uploads/2026/11/November-2026.pdf"
    rows = [{"mosque_id": 1, "day": "2026-11-20", "jumuah": "12:30, 13:30", "source_id": 9, "source_url": friday},
            {"mosque_id": 2, "day": "2026-11-20", "jumuah": "12:30, 13:30", "source_id": 8, "source_url": friday.replace("x.org", "y.org")},
            {"mosque_id": 3, "day": "2026-11-20", "jumuah": "12:15, 13:15", "source_id": 7, "source_url": "https://z.org/"}]
    mosques = {1: {"id": 1, "name": "A", "jumuah": None, "jumuah_hash": None, "profile_at": "x"},
               2: {"id": 2, "name": "B", "jumuah": None, "jumuah_hash": None, "profile_at": "x"},
               3: {"id": 3, "name": "C", "jumuah": None, "jumuah_hash": None, "profile_at": "x"}}

    class FakeStore:
        def _req(self, method, path, headers=None, json=None):
            if method == "GET" and path.startswith("jamaat_times"):
                return rows
            if method == "GET" and path.startswith("mosques"):
                return [dict(m) for m in mosques.values()]
            if method == "GET":
                return []
            mosques[int(path.split("eq.")[1])].update(json)
    reads = []

    def fake_poster(m, url, reader, today, why=None):
        reads.append(url)
        if "x.org" in url:
            return {"slots": [{"name": "1st Jumu'ah", "khutbah": None, "jamaat": "12:30"},
                              {"name": "2nd Jumu'ah", "khutbah": None, "jamaat": "13:30"}]}
        why.append('AI: {"slots": []}')
        return None
    monkeypatch.setattr(P, "read_poster", fake_poster)
    log = P.fill_from_timetables(FakeStore(), date(2026, 11, 19), reader=object())
    assert [s["jamaat"] for s in mosques[1]["jumuah"]["slots"]] == ["12:30", "13:30"]
    assert mosques[1]["jumuah_hash"] == P.TIMETABLE_FILE and len(log) == 1
    assert mosques[2]["jumuah"] is None and mosques[2]["jumuah_hash"].startswith(P.TRIED)
    assert mosques[3]["jumuah"] is None and len(reads) == 2          # a web page cell is not sent to the AI
    P.fill_from_timetables(FakeStore(), date(2026, 11, 19), reader=object())
    assert len(reads) == 2                                            # each file is read only once
    assert P.fill_from_timetables(FakeStore(), date(2026, 11, 19)) == []   # no AI reader: unchanged behaviour


def test_file_named_after_a_month_is_a_timetable_candidate():
    """usmanimosque.com/namaz-timetable/ links only uploads/2026/11/November.pdf (no 'timetable' in link or name)."""
    html = (b'<html><body><h1>Namaz Timetable</h1><a href="/wp-content/uploads/2026/11/November.pdf"></a>'
            b'<a href="/wp-content/uploads/2026/11/Timetable.pdf">Timetable</a>'
            b'<a href="/wp-content/uploads/2026/11/11-Nov-2026.jpg">x</a>'
            b'<a href="/wp-content/uploads/2026/11/logo.png">logo</a><a href="/wp-content/uploads/menu.pdf">Menu</a></body></html>')
    _, links = page_text(html, "https://usmanimosque.com/namaz-timetable/")
    assert links == ["https://usmanimosque.com/wp-content/uploads/2026/11/Timetable.pdf",
                     "https://usmanimosque.com/wp-content/uploads/2026/11/November.pdf",
                     "https://usmanimosque.com/wp-content/uploads/2026/11/11-Nov-2026.jpg"]


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


def test_prayer_screen_with_colon_and_am_pm_in_separate_boxes():
    """MasjidBox (Nov 2026) prints '7', ':', '00', 'AM' in four boxes — 71 mosques read 'no times' for a day."""
    from collector.extract import page_text, has_times
    rows = [("Fajr", 7, "00", "AM"), ("Dhuhr", 1, "00", "PM"), ("Asr", 2, "45", "PM"), ("Maghrib", 4, "04", "PM"),
            ("Isha", 7, "30", "PM"), ("Jumuah\n1", 12, "00", "PM")]
    html = ("<html><body><div>The Iqamah of</div><div>Maghrib</div><div>is in</div><div>01</div><div>Hours</div>"
            + "".join(f"<div>{p}</div><div>{h}</div><div>:</div><div>{m}</div><div>{ap}</div>" for p, h, m, ap in rows)
            + "</body></html>").encode()
    text, _ = page_text(html, "https://masjidbox.com/prayer-times/x")
    assert "Fajr\n7:00 AM" in text and "Asr\n2:45 PM" in text and "Isha\n7:30 PM" in text and has_times(text)
    assert "01\nHours" in text and "Jumuah\n1\n12:00 PM" in text
    # 24-hour screens with the colon box, no AM/PM
    html = "".join(f"<div>{p}</div><div>{h}</div><div>:</div><div>{m}</div>" for p, h, m in
                   [("Fajr", 5, 35), ("Dhuhr", 11, 48), ("Asr", 13, 39), ("Maghrib", 16, "00"), ("Isha", 17, 54)])
    text, _ = page_text(f"<html><body>{html}</body></html>".encode(), "https://masjidbox.com/prayer-times/y")
    assert "Asr\n13:39\nMaghrib\n16:00" in text


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


def test_ask_retries_cut_off_answer_then_raises():
    import threading
    import pytest
    from types import SimpleNamespace
    from collector.extract import AIReplyError, Reader

    def fake_reader(replies):
        r = Reader.__new__(Reader)
        r.calls, r.max_calls, r._lock, r.sizes = 0, 10, threading.Lock(), []

        def create(**kw):
            r.sizes.append(kw["max_tokens"])
            text = replies.pop(0)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")
        r.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        return r

    r = fake_reader(['{"jumuah": {"slots": [{"name": "1st", "jam', '{"jumuah": null, "sisters": "yes"}'])
    assert r.ask("q", max_tokens=800) == {"jumuah": None, "sisters": "yes", "days": {}}
    assert r.sizes == [800, 4000] and r.calls == 2
    r = fake_reader(["sorry", "still no json"])
    with pytest.raises(AIReplyError):            # never saved as "nothing found"
        r.ask("q", max_tokens=800)


def test_page_text_adds_address_from_map_link_and_footer_without_changing_hash():
    # slmcel.org.uk ("SLMCEL Masjid Darul Ilm") shows its address only in a map link and the footer;
    # without it the reader refused the page as not clearly the Sri Lankan Muslim Centre.
    body = ("<html><body><h1>SLMCEL Masjid Darul Ilm</h1><p>Fajr 05:21</p>"
            "<a href='https://maps.google.com/?q=16-18%20Pilgrims%20Way%2C%20East%20Ham%2C%20London%20E6%201HW'>Visit</a>"
            "<a href='https://www.google.com/maps/@51.5,0.05,15z'>map</a>"
            "<footer><p>Contact us</p><p>16-18 Pilgrims Way, London E6 1HW</p></footer></body></html>")
    text, _ = page_text(body.encode(), "https://www.slmcel.org.uk/")
    assert "[Address shown on this site: 16-18 Pilgrims Way, East Ham, London E6 1HW]" in text
    assert "[Address shown on this site: 16-18 Pilgrims Way, London E6 1HW]" in text
    assert "Contact us" not in text                       # footer itself still left out
    no_addr = body.replace("maps.google.com", "example.org").replace("E6 1HW</p></footer>", "</p></footer>")
    t2, _ = page_text(no_addr.encode(), "https://www.slmcel.org.uk/")
    assert "Address shown" not in t2
    assert fingerprint_text(text) == fingerprint_text(text.split("\n[Address")[0])


def test_ai_prompt_says_a_time_printed_once_covers_the_days_it_spans():
    # Central Mosque Rochdale's Hijri-month PDF prints Fajr/Zuhr/Asr iqamah once a week in merged cells;
    # the AI returned Isha only for the days between, so every such day failed "fewer than two times".
    from collector.extract import PROMPT
    text = PROMPT.format(name="M", where="W", today="2026-11-18")
    assert "merged cell" in text and "next printed time" in text
    assert "applies to those dates only" in text          # a Fridays-only box is not spread over the week


def test_poster_reply_cut_off_is_asked_again_with_more_room(monkeypatch):
    """read_poster used max_tokens=500: answers came back empty or cut off, so no poster ever gave Jumu'ah."""
    from datetime import date
    import collector.profile as P
    import collector.extract as E
    monkeypatch.setattr(P.fetch, "get", lambda url: (b"%PDF-1.4 x", "application/pdf", url))
    monkeypatch.setattr(E, "prepare_media", lambda data, mt: (data, mt))
    calls = []

    class Msg:
        def __init__(self, t):
            self.content = [type("B", (), {"type": "text", "text": t})()]

    class Client:
        class messages:
            @staticmethod
            def create(model, max_tokens, messages):
                calls.append(max_tokens)
                return Msg('{"slots": [{"name": "1st Jum' if len(calls) == 1 else
                           '{"slots": [{"name": "1st Jumu\'ah", "khutbah": null, "jamaat": "12:45"},'
                           ' {"name": "2nd Jumu\'ah", "khutbah": null, "jamaat": "13:10"}], "note": null}')

    class R:
        client = Client()
        def _count(self):
            pass
    why = []
    j = P.read_poster({"name": "Test"}, "https://x.org/Nov-Timetable.pdf", R(), date(2026, 11, 19), why)
    assert [s["jamaat"] for s in j["slots"]] == ["12:45", "13:10"]
    assert calls[0] >= 2000 and calls[1] > calls[0]


def test_fiveprayers_screen_is_followed():
    # Southport Mosque's homepage links its FivePrayers screen (iqama + Jumu'ah 12:45)
    from collector.learn import screen_host
    assert screen_host("http://fiveprayers.org/display/index.php?id=mohammad")
    assert not screen_host("https://fiveprayersonline.example.com/")
