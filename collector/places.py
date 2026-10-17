"""Find timetables for mosques that have no website on file, using Google Places.

    python -m collector.places --limit 100

For each mosque without a timetable source:
  1. Places Text Search ("<name> <postcode>", type mosque) → the matching place,
     checked by postcode. Only the place ID is saved (Google's terms allow
     keeping place IDs; other Places data may not be stored).
  2. Place Details → the mosque's website, used straight away and not saved.
  3. collector.learn reads the mosque's own site and, if it finds jamaat times,
     saves that timetable page (an address on the mosque's own website) as a source.
Mosques are re-checked every 60 days, so newly added websites get picked up.

Needs GOOGLE_PLACES_KEY (Google Cloud → APIs & Services → enable "Places API (New)").
"""
import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from . import config, fetch
from .extract import Reader
from .learn import Learner
from .store import Store

KEY = os.environ.get("GOOGLE_PLACES_KEY", "")
API = "https://places.googleapis.com/v1"


def norm_pc(s: str) -> str:
    return re.sub(r"\s", "", (s or "").upper())


def find_place_id(m: dict) -> str | None:
    r = requests.post(f"{API}/places:searchText", timeout=30,
                      headers={"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": "places.id,places.formattedAddress,places.types"},
                      json={"textQuery": f"{m['name']} {m['postcode']}", "regionCode": "GB", "includedType": "mosque"})
    r.raise_for_status()
    pc = norm_pc(m["postcode"])
    outward = pc[:-3]
    best = None
    for p in r.json().get("places", []):
        addr = norm_pc(p.get("formattedAddress", ""))
        if pc in addr:
            return p["id"]                       # same postcode: confident match
        if best is None and outward and re.search(outward + r"\d[A-Z]{2}", addr) and "mosque" in p.get("types", []):
            best = p["id"]                       # a mosque in the same postcode district
    return best


def website_of(place_id: str) -> str | None:
    r = requests.get(f"{API}/places/{place_id}", timeout=30,
                     headers={"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": "websiteUri"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("websiteUri")


SOCIAL = re.compile(r"facebook|instagram|twitter|x\.com|linktr|wa\.me|google\.", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100, help="mosques to look up this run")
    args = ap.parse_args()
    if not KEY:
        raise SystemExit("Set GOOGLE_PLACES_KEY to use Google Places")
    store = Store()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    have = {r["mosque_id"] for r in store._req("GET", "sources?select=mosque_id&enabled=is.true", headers={"Range": "0-19999"})}
    mosques = store._req("GET", "mosques?select=id,name,address,town,postcode,lat,lng,place_id,places_checked_at"
                                f"&active=is.true&website=is.null&or=(places_checked_at.is.null,places_checked_at.lt.{cutoff})"
                                "&order=places_checked_at.asc.nullsfirst,id", headers={"Range": "0-9999"})
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    learner = Learner(Reader(max_calls=config.MAX_AI_CALLS_LEARN), today)
    done = 0
    for m in mosques:
        if m["id"] in have:
            continue
        if done >= args.limit:
            break
        done += 1
        now = datetime.now(timezone.utc).isoformat()
        try:
            pid = m.get("place_id") or find_place_id(m)
            store._req("PATCH", f"mosques?id=eq.{m['id']}", json={"place_id": pid, "places_checked_at": now},
                       headers={"Prefer": "return=minimal"})
            if not pid:
                print(f"{m['name']}: no matching place")
                continue
            site = website_of(pid)
            if not site or SOCIAL.search(site):
                print(f"{m['name']}: no website on Google")
                continue
            recipe, msg, days, tt_url = learner.learn(m, site)
            if recipe["kind"] == "none":
                print(f"{m['name']}: website has no jamaat times ({msg[:80]})")
                continue
            store.upsert("sources", [{"mosque_id": m["id"], "url": tt_url, "recipe": recipe, "recipe_status": "ok",
                                      "recipe_learned_at": now, "last_message": msg[:300]}], "mosque_id,url")
            src = store._req("GET", f"sources?select=id&mosque_id=eq.{m['id']}&url=eq.{requests.utils.quote(tt_url, safe='')}")
            if days and src:
                store.save_days(m["id"], src[0]["id"], tt_url, days)
            print(f"{m['name']}: {recipe['kind']} — {msg}")
        except fetch.Blocked as e:
            print(f"{m['name']}: website blocks automated access ({e})")
        except Exception as e:
            print(f"{m['name']}: failed — {str(e)[:120]}")
            if "AI call limit" in str(e):
                break


if __name__ == "__main__":
    main()
