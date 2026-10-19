"""Check every mosque listing is still current, using Google Places.

    python -m collector.verify --limit 1000

For each mosque (each re-checked every 90 days):
  1. search Google Places for "<name> <postcode>"; a result counts as this mosque
     if it has the same postcode, or is a place of worship within 150 m
  2. if that finds nothing, look for any mosque within 150 m of the postcode
     (catches mosques that changed name)
  3. record the outcome:
        open          Google lists it as operating
        renamed?      a mosque is there but under a different name — check by hand
        closed_temp   temporarily closed
        closed        permanently closed  → hidden from the app
        not_found     nothing at this address on Google
Only the Google place ID and our own outcome are stored (Google's terms don't
allow keeping its names, addresses or phone numbers). The run summary lists
every listing that needs a human look.

Needs GOOGLE_PLACES_KEY.
"""
import argparse
import math
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from .places import API, KEY, norm_pc
from .store import Store

FIELDS = "places.id,places.displayName,places.formattedAddress,places.businessStatus,places.types,places.location"
WORSHIP = {"mosque", "place_of_worship", "church", "hindu_temple", "synagogue"}
STOP = {"masjid", "mosque", "jamia", "jame", "jami", "islamic", "centre", "center", "the", "and", "of", "trust",
        "association", "community", "education", "educational", "cultural", "society", "madrasa", "madrassa",
        "madrasah", "uk", "e", "ul", "al", "jamea", "musjid", "markazi"}


def metres(a_lat, a_lng, b_lat, b_lng):
    r = 6371000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def words(name: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", name.lower()) if w not in STOP}


def search(body: dict) -> list:
    r = requests.post(f"{API}/places:searchText", timeout=30, json=body,
                      headers={"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": FIELDS})
    r.raise_for_status()
    return r.json().get("places", [])


def judge(m: dict) -> tuple[str, str | None, str]:
    """Returns (outcome, place_id, note). Google's own text is used here only to decide, never stored."""
    pc = norm_pc(m["postcode"])
    found = search({"textQuery": f"{m['name']} {m['postcode']}", "regionCode": "GB",
                    "locationBias": {"circle": {"center": {"latitude": m["lat"], "longitude": m["lng"]}, "radius": 2000.0}}})
    best = None
    for p in found:
        loc = p.get("location") or {}
        d = metres(m["lat"], m["lng"], loc.get("latitude", 0), loc.get("longitude", 0)) if loc else 1e9
        same_pc = pc in norm_pc(p.get("formattedAddress", ""))
        worship = bool(WORSHIP & set(p.get("types", [])))
        if same_pc or (d <= 150 and worship):
            best = (p, d)
            break
    renamed = False
    if not best:
        near = search({"textQuery": "mosque", "regionCode": "GB", "includedType": "mosque",
                       "locationRestriction": {"rectangle": {
                           "low": {"latitude": m["lat"] - 0.0015, "longitude": m["lng"] - 0.0025},
                           "high": {"latitude": m["lat"] + 0.0015, "longitude": m["lng"] + 0.0025}}}})
        for p in near:
            loc = p.get("location") or {}
            d = metres(m["lat"], m["lng"], loc.get("latitude", 0), loc.get("longitude", 0))
            if d <= 150:
                best, renamed = (p, d), True
                break
    if not best:
        return "not_found", None, "nothing on Google at this postcode"
    p, d = best
    status = p.get("businessStatus") or "OPERATIONAL"
    if status == "CLOSED_PERMANENTLY":
        return "closed", p["id"], "Google: permanently closed"
    if status == "CLOSED_TEMPORARILY":
        return "closed_temp", p["id"], "Google: temporarily closed"
    gname = (p.get("displayName") or {}).get("text", "")
    if renamed or (words(m["name"]) and not (words(m["name"]) & words(gname))):
        return "renamed?", p["id"], f"a mosque {int(d)} m away is listed under another name"
    return "open", p["id"], ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--hide-closed", action="store_true", default=True)
    args = ap.parse_args()
    if not KEY:
        raise SystemExit("Set GOOGLE_PLACES_KEY to check listings with Google Places")
    store = Store()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = store._req("GET", "mosques?select=id,name,town,postcode,lat,lng,source_note"
                             f"&active=is.true&or=(verified_at.is.null,verified_at.lt.{cutoff})"
                             "&order=verified_at.asc.nullsfirst,id", headers={"Range": f"0-{args.limit - 1}"})
    counts, review, failures = {}, [], []
    for m in rows:
        try:
            outcome, pid, note = judge(m)
        except Exception as e:
            detail = getattr(getattr(e, "response", None), "text", "") or str(e)
            failures.append(detail[:400])
            print(f"{m['name']}: lookup failed — {detail[:300]}")
            if len(failures) >= 5 and not counts:
                msg = ("## Listing check stopped — Google refused every lookup\n\nGoogle's message:\n\n```\n"
                       + failures[0] + "\n```\n\nUsually: Places API (New) not enabled on the key's project, "
                       "the key restricted to a different API, or billing not linked.")
                print(msg)
                if os.environ.get("GITHUB_STEP_SUMMARY"):
                    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
                        fh.write(msg + "\n")
                raise SystemExit(1)
            continue
        now = datetime.now(timezone.utc).isoformat()
        f = {"verify_status": outcome, "verify_note": note or None, "verified_at": now}
        if pid:
            f["place_id"] = pid
        if outcome == "closed" and args.hide_closed:
            f["active"] = False
        store._req("PATCH", f"mosques?id=eq.{m['id']}", json=f, headers={"Prefer": "return=minimal"})
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome != "open":
            review.append(f"{m['town']} — {m['name']} ({m['postcode']}): {outcome} — {note}")
    summary = ("## Listing check (Google Places)\n\n" + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
               + "\n\nNeeds a look:\n" + "\n".join(f"- {r}" for r in sorted(review)))
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(summary + "\n")


if __name__ == "__main__":
    main()
