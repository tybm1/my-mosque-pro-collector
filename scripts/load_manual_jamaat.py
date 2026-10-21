"""Load hand-entered jamaat timetables from manual/*.csv (mosque_id,day,fajr,dhuhr,asr,maghrib,isha) into jamaat_times.
Rows are upserted, so re-running is safe. Only future/today rows are loaded."""
import csv, glob
from datetime import date
from collector.store import Store

rows = []
for f in sorted(glob.glob("manual/*.csv")):
    for r in csv.DictReader(open(f)):
        if r["day"] >= date.today().isoformat():
            rows.append({**{k: r[k] for k in ("day", "fajr", "dhuhr", "asr", "maghrib", "isha")},
                         "mosque_id": int(r["mosque_id"]), "source_url": "High Wycombe Mosque app (entered by hand)"})
Store().upsert("jamaat_times", rows, "mosque_id,day")
print(f"loaded {len(rows)} hand-entered days")
