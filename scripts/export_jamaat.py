"""Export collected jamaat times (yesterday to +6 days) for active mosques to exports/jamaat.csv."""
import csv, os
from datetime import date, timedelta
from collector.store import Store

store = Store()
start = (date.today() - timedelta(days=1)).isoformat()
rows, off = [], 0
while True:
    page = store._req("GET", "jamaat_times?select=mosque_id,day,fajr,dhuhr,asr,maghrib,isha,jumuah,source_url,"
                             "mosques!inner(name,town,postcode,active)&mosques.active=is.true"
                             f"&day=gte.{start}&order=mosque_id,day", headers={"Range": f"{off}-{off + 999}"})
    rows += page
    if len(page) < 1000:
        break
    off += 1000
os.makedirs("exports", exist_ok=True)
with open("exports/jamaat.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["mosque_id", "name", "town", "postcode", "day", "fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah", "source_url"])
    for r in rows:
        m = r["mosques"]
        w.writerow([r["mosque_id"], m["name"], m["town"], m["postcode"], r["day"],
                    *[(r[k] or "")[:5] for k in ("fajr", "dhuhr", "asr", "maghrib", "isha")],
                    (r.get("jumuah") or "")[:5] if isinstance(r.get("jumuah"), str) else (r.get("jumuah") or ""),
                    r.get("source_url") or ""])
print(len(rows), "rows")
