"""One-off: load the mosques and starting timetable sources into Supabase.
    python -m collector.load_seed
Safe to run again — it updates existing rows instead of duplicating them."""
import csv
import json
from pathlib import Path

from .store import Store

SEED = Path(__file__).resolve().parent.parent / "seed"


def main():
    store = Store()
    mosques = json.loads((SEED / "mosques.json").read_text())
    store.upsert("mosques", mosques, "id")
    print(f"Loaded {len(mosques)} mosques")
    rows = []
    with open(SEED / "sources.csv") as f:
        for r in csv.DictReader(f):
            rows.append({"mosque_id": int(r["mosque_id"]), "url": r["url"].strip(),
                         "url_pattern": (r.get("url_pattern") or "").strip() or None})
    # Mosques whose website is already known get their home page as a starting source.
    known = {(r["mosque_id"], r["url"]) for r in rows}
    for m in mosques:
        if m.get("website") and not any(k[0] == m["id"] for k in known):
            rows.append({"mosque_id": m["id"], "url": m["website"], "url_pattern": None})
    store.upsert("sources", rows, "mosque_id,url")
    print(f"Loaded {len(rows)} timetable sources")


if __name__ == "__main__":
    main()
