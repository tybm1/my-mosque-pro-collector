"""Daily coverage check: did every mosque we can read get today's jamaat times?

    python -m collector.check [--strict] [--min 95]

Expected = active mosques with an enabled, readable timetable source (a learned recipe or an AI reader).
Lists every expected mosque still missing today's times, plus sources that were switched off or blocked
in the last 7 days. With --strict the step fails (and GitHub emails the repo owner) when coverage is below --min %.
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from . import config
from .store import Store

READABLE = {"table", "fields", "mawaqit", "page_ai", "file_ai"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--min", type=float, default=95.0)
    args = ap.parse_args()
    store = Store()
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    srcs, start = [], 0
    while True:
        page = store._req("GET", "sources?select=id,mosque_id,url,enabled,blocked_reason,recipe,url_pattern,last_status,last_message,"
                                 "last_checked,fail_count,mosques!inner(name,town,active)&mosques.active=is.true",
                          headers={"Range": f"{start}-{start + 999}"})
        srcs += page
        if len(page) < 1000:
            break
        start += 1000
    readable = [s for s in srcs if s["enabled"] and not s["blocked_reason"]
                and ((s.get("recipe") or {}).get("kind") in READABLE or s.get("url_pattern"))]
    expected = {s["mosque_id"]: s for s in readable}
    have = store.mosques_with_day(today)
    missing = [s for mid, s in expected.items() if mid not in have]
    cov = 100.0 * (len(expected) - len(missing)) / max(1, len(expected))
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    lost = [s for s in srcs if (not s["enabled"] or s["blocked_reason"]) and (s.get("last_checked") or "") >= week
            and s["mosque_id"] not in have]
    lines = [f"## Daily check — {today}",
             f"**{len(expected) - len(missing)} of {len(expected)} readable mosques have today's times ({cov:.1f}%).** "
             f"Target: {args.min:.0f}%.", ""]
    if missing:
        lines.append(f"### Missing today ({len(missing)})")
        for s in sorted(missing, key=lambda s: (s["mosques"]["town"], s["mosques"]["name"])):
            lines.append(f"- {s['mosques']['name']} ({s['mosques']['town']}) — {s.get('last_status') or '?'}: "
                         f"{(s.get('last_message') or '')[:140]} — {s['url']}")
    if lost:
        lines += ["", f"### Switched off or blocked in the last 7 days ({len(lost)})"]
        for s in lost:
            lines.append(f"- {s['mosques']['name']} ({s['mosques']['town']}) — "
                         f"{s.get('blocked_reason') or 'disabled after repeated errors'} — {s['url']}")
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(text + "\n")
    try:
        store._req("POST", "collector_runs", json={"notes": text[:20000], "checked": len(expected),
                                                    "changed": len(expected) - len(missing), "failed": len(missing)},
                   headers={"Prefer": "return=minimal"})
    except Exception as e:
        print(f"(could not save check: {e})")
    if args.strict and cov < args.min:
        print(f"::error::Only {cov:.1f}% of mosques have today's jamaat times (target {args.min:.0f}%).")
        sys.exit(1)


if __name__ == "__main__":
    main()
