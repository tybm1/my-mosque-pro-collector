"""Talks to Supabase over its REST API using the service key (server-side only)."""
import requests

from . import config


class Store:
    def __init__(self):
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
            raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        self.base = config.SUPABASE_URL + "/rest/v1/"
        self.h = {"apikey": config.SUPABASE_SERVICE_KEY,
                  "Authorization": "Bearer " + config.SUPABASE_SERVICE_KEY,
                  "Content-Type": "application/json"}

    def _req(self, method, path, **kw):
        r = requests.request(method, self.base + path, headers={**self.h, **kw.pop("headers", {})}, timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else None

    def due_sources(self) -> list[dict]:
        rows, start = [], 0
        while True:
            page = self._req("GET", "sources?select=*,mosques(id,name,town,lat,lng)"
                                    "&enabled=is.true&blocked_reason=is.null&order=id",
                             headers={"Range": f"{start}-{start + 999}"})
            rows += page
            if len(page) < 1000:
                return rows
            start += 1000

    def update_source(self, sid: int, fields: dict):
        self._req("PATCH", f"sources?id=eq.{sid}", json=fields, headers={"Prefer": "return=minimal"})

    def save_days(self, mosque_id: int, source_id: int, url: str, days: dict) -> int:
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
