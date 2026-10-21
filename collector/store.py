"""Talks to Supabase over its REST API using the service key (server-side only)."""
import requests

from . import config


class Store:
    def __init__(self):
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
            raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        self.base = config.SUPABASE_URL + "/rest/v1/"
        key = config.SUPABASE_SERVICE_KEY
        self.h = {"apikey": key, "Content-Type": "application/json"}
        if not key.startswith("sb_"):            # legacy service_role JWT; new sb_secret_ keys go in apikey only
            self.h["Authorization"] = "Bearer " + key

    def _req(self, method, path, **kw):
        r = requests.request(method, self.base + path, headers={**self.h, **kw.pop("headers", {})}, timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else None

    def due_sources(self) -> list[dict]:
        rows, start = [], 0
        while True:
            page = self._req("GET", "sources?select=*,mosques!inner(id,name,address,town,postcode,lat,lng)"
                                    "&enabled=is.true&blocked_reason=is.null&mosques.active=is.true&order=id",
                             headers={"Range": f"{start}-{start + 999}"})
            rows += page
            if len(page) < 1000:
                return rows
            start += 1000

    def mosques_with_day(self, day: str) -> set[int]:
        have, start = set(), 0
        while True:
            page = self._req("GET", f"jamaat_times?select=mosque_id&day=eq.{day}", headers={"Range": f"{start}-{start + 999}"})
            have |= {r["mosque_id"] for r in page}
            if len(page) < 1000:
                return have
            start += 1000

    def update_source(self, sid: int, fields: dict):
        self._req("PATCH", f"sources?id=eq.{sid}", json=fields, headers={"Prefer": "return=minimal"})

    def save_days(self, mosque_id: int, source_id: int, url: str, days: dict) -> int:
        days = fill_change_dates(days)
        rows = [{"mosque_id": mosque_id, "day": d, **{k: v for k, v in t.items()},
                 "source_id": source_id, "source_url": url} for d, t in days.items()]
        for i in range(0, len(rows), 500):
            self._req("POST", "jamaat_times?on_conflict=mosque_id,day", json=rows[i:i + 500],
                      headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return len(rows)

    def upsert(self, table: str, rows: list[dict], conflict: str):
        for i in range(0, len(rows), 500):
            self._req("POST", f"{table}?on_conflict={conflict}", json=rows[i:i + 500],
                      headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def start_run(self) -> int:
        return self._req("POST", "collector_runs", json={}, headers={"Prefer": "return=representation"})[0]["id"]

    def finish_run(self, run_id: int, stats: dict):
        from datetime import datetime, timezone
        self._req("PATCH", f"collector_runs?id=eq.{run_id}",
                  json={**stats, "finished_at": datetime.now(timezone.utc).isoformat()},
                  headers={"Prefer": "return=minimal"})

    def prune(self):
        self._req("POST", "rpc/prune_old_jamaat", json={})


def fill_change_dates(days: dict, hold: int = 6) -> dict:
    """Some mosques list times by the date they change ("from 19 Oct ..., from 25 Oct ..."), not one row per day.
    When a read has gaps between its dates, each missing day takes the times of the latest listed date before it,
    and the last listed date carries on for up to `hold` more days (until the next change is published).
    A timetable that lists every day (no gaps) is returned unchanged."""
    from datetime import date, timedelta
    keys = sorted(k for k in days if k)
    try:
        ds = [date.fromisoformat(k) for k in keys]
    except ValueError:
        return days
    if len(ds) < 2 or all((b - a).days == 1 for a, b in zip(ds, ds[1:])):
        return days                                   # one day, or a normal day-by-day timetable
    out = dict(days)
    for a, b, k in zip(ds, ds[1:] + [ds[-1] + timedelta(days=hold + 1)], keys):
        d = a + timedelta(days=1)
        while d < b:
            row = dict(days[k])
            row["maghrib"] = None                     # follows sunset, which moves daily: never copy it forward
            if "jumuah" in row and d.weekday() != 4:
                row["jumuah"] = None
            out.setdefault(d.isoformat(), row)
            d += timedelta(days=1)
    return out
