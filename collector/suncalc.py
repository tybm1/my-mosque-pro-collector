"""Sunrise, solar noon and sunset (NOAA method, accurate to about a minute in the UK).
Used only to sanity-check timetables — e.g. Maghrib jamaat must be just after sunset."""
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def _sun(day: date, lat: float, lng: float):
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)                       # fractional year (noon)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    noon_utc_min = 720 - 4 * lng - eqt
    zen = math.radians(90.833)
    phi = math.radians(lat)
    cos_ha = (math.cos(zen) / (math.cos(phi) * math.cos(decl))) - math.tan(phi) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))
    return noon_utc_min - 4 * ha, noon_utc_min, noon_utc_min + 4 * ha   # minutes after 00:00 UTC


def sun_times(day: date, lat: float, lng: float) -> dict[str, int]:
    """Local (UK clock) minutes-after-midnight for sunrise, noon and sunset."""
    base = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo("UTC"))
    out = {}
    for name, mins in zip(("sunrise", "noon", "sunset"), _sun(day, lat, lng)):
        local = (base + timedelta(minutes=mins)).astimezone(LONDON)
        out[name] = local.hour * 60 + local.minute
    return out
