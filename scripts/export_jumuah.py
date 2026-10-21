"""Export Jumu'ah times for active mosques to exports/jumuah.csv (one row per Jumu'ah slot)."""
import csv, os
from collector.store import Store

store = Store()
rows, off = [], 0
while True:
    page = store._req("GET", "mosques?select=id,name,town,postcode,jumuah,jumuah_hash"
                             "&active=is.true&jumuah=not.is.null&order=town,name", headers={"Range": f"{off}-{off + 999}"})
    rows += page
    if len(page) < 1000:
        break
    off += 1000
os.makedirs("exports", exist_ok=True)
with open("exports/jumuah.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["mosque_id", "name", "town", "postcode", "slot", "khutbah", "jamaat", "note", "source", "entered_by_hand"])
    for m in rows:
        j = m["jumuah"] or {}
        for s in j.get("slots") or []:
            w.writerow([m["id"], m["name"], m["town"], m["postcode"], s.get("name") or "", s.get("khutbah") or "",
                        s.get("jamaat") or "", j.get("note") or "", j.get("src") or "", "yes" if m.get("jumuah_hash") == "manual" else ""])
print(len(rows), "mosques")
