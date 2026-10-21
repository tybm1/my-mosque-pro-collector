"""One-off/idempotent: re-filter saved Jumu'ah notes so only worshipper-facing sentences remain."""
from collector.profile import user_note
from collector.store import Store

store = Store()
rows = store._req("GET", "mosques?select=id,jumuah&jumuah=not.is.null", headers={"Range": "0-9999"})
changed = 0
for m in rows:
    j = m["jumuah"] or {}
    new = user_note(j.get("note"))
    if new != j.get("note"):
        store._req("PATCH", f"mosques?id=eq.{m['id']}", json={"jumuah": {**j, "note": new}}, headers={"Prefer": "return=minimal"})
        changed += 1
print(f"{changed} of {len(rows)} notes cleaned")
