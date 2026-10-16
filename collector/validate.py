"""Sanity checks so a misread timetable never reaches the app.

A day is kept only if every jamaat time it has is in order and plausible for
that mosque's location on that date (checked against the real sunrise and
sunset). If too many days in one timetable fail, the whole timetable is
rejected — that usually means the wrong column (start times) was read.
"""
import re
from datetime import date, timedelta

from .suncalc import sun_times

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
_TIME = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")


def to_minutes(value, prayer: str) -> int | None:
    """'13:30', '1.30', '01:30' → minutes after midnight. Afternoon prayers written
    on a 12-hour clock (e.g. Isha '8:15') are moved to the afternoon."""
    if value is None:
        return None
    m = _TIME.match(str(value))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    if prayer in ("dhuhr",) and h < 10:
        h += 12                       # '1:30' Dhuhr means 13:30 (12:xx stays)
    elif prayer in ("asr", "maghrib", "isha") and h < 12:
        h += 12
    return h * 60 + mi


def fmt(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def check_day(day: date, row: dict, lat: float, lng: float) -> tuple[dict | None, str | None]:
    """Return (clean_row, None) or (None, reason)."""
    t = {p: to_minutes(row.get(p), p) for p in PRAYERS}
    if sum(v is not None for v in t.values()) < 2:
        return None, "fewer than two jamaat times"
    sun = sun_times(day, lat, lng)
    # Order: each present time later than the one before it.
    seq = [t[p] for p in PRAYERS if t[p] is not None]
    if any(b <= a for a, b in zip(seq, seq[1:])):
        return None, "times out of order"
    if t["fajr"] is not None and not (sun["sunrise"] - 240 <= t["fajr"] < sun["sunrise"]):
        return None, "Fajr not before sunrise"
    if t["dhuhr"] is not None and not (sun["noon"] - 2 <= t["dhuhr"] <= sun["noon"] + 150):
        return None, "Dhuhr not after midday"
    if t["asr"] is not None and not (sun["noon"] + 60 <= t["asr"] < sun["sunset"]):
        return None, "Asr not between midday and sunset"
    if t["maghrib"] is not None and not (sun["sunset"] - 3 <= t["maghrib"] <= sun["sunset"] + 45):
        return None, "Maghrib not just after sunset"
    if t["isha"] is not None and not (sun["sunset"] + 40 <= t["isha"] <= 24 * 60 - 1):
        return None, "Isha too early or after midnight"
    clean = {p: fmt(t[p]) if t[p] is not None else None for p in PRAYERS}
    j = row.get("jumuah")
    clean["jumuah"] = str(j)[:60] if j and day.weekday() == 4 else None
    return clean, None


def check_timetable(days: dict, lat: float, lng: float, today: date) -> tuple[dict, list[str], bool]:
    """days: {'YYYY-MM-DD': {fajr, dhuhr, asr, maghrib, isha, jumuah}}.
    Returns (clean_days, problems, accepted)."""
    clean, problems = {}, []
    lo, hi = today - timedelta(days=3), today + timedelta(days=62)
    considered = 0
    for key, row in sorted(days.items()):
        try:
            d = date.fromisoformat(key)
        except ValueError:
            problems.append(f"{key}: not a date")
            continue
        if not (lo <= d <= hi) or not isinstance(row, dict):
            continue
        considered += 1
        ok, why = check_day(d, row, lat, lng)
        if ok:
            clean[key] = ok
        else:
            problems.append(f"{key}: {why}")
    accepted = considered > 0 and len(clean) >= 0.7 * considered
    return (clean if accepted else {}), problems, accepted
