"""Read-only: for the source ids in scripts/debug_sources.txt, follow the recipe with plain code (no AI, no saving)
and write what it reads plus the validation problems to exports/pages/debug_<id>.txt. For PDFs, also the PDF text."""
import json, os
from datetime import datetime
from zoneinfo import ZoneInfo
from collector import config, fetch
from collector.run import file_for, recipe_read
from collector.recipes import pdf_tables
from collector.store import Store
from collector.validate import check_timetable

os.makedirs("exports/pages", exist_ok=True)
path = "scripts/debug_sources.txt"
ids = [int(x) for x in open(path).read().split()] if os.path.exists(path) else []
today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
store = Store()
for sid in ids:
    out = [f"source {sid}  today {today}"]
    try:
        src = store._req("GET", f"sources?id=eq.{sid}&select=*,mosques!inner(id,name,lat,lng)")[0]
        r = src.get("recipe") or {}
        out.append(json.dumps(r))
        if r.get("link_from"):
            href, label = file_for(r, today)
            out.append(f"file: {href}  label: {label}")
            data, _, _ = fetch.get(href)
            if data[:4] == b"%PDF":
                tables, text = pdf_tables(data)
                out.append("--- pdf text ---\n" + text[:12000])
                for i, t in enumerate(tables[:3]):
                    out.append(f"--- table {i} ---\n" + "\n".join(" | ".join(str(c) for c in row) for row in t[:45]))
        if r.get("kind") in ("table", "fields", "mawaqit"):
            days, used = recipe_read(src, today)
            out.append("--- recipe days ---\n" + json.dumps(days, indent=0, sort_keys=True))
            m = src["mosques"]
            clean, problems, ok = check_timetable(days, m["lat"], m["lng"], today)
            out.append(f"accepted={ok} clean={sorted(clean)}\nproblems={problems}")
    except Exception as e:
        out.append(f"failed: {type(e).__name__}: {e}")
    open(f"exports/pages/debug_{sid}.txt", "w").write("\n".join(out))
    print(sid, "debugged")
