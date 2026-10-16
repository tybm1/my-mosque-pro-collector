"""Find the official website and timetable page for mosques that don't have one yet.

    python -m collector.discover --limit 200

For each mosque without a source it searches the web (Brave Search API), skips
directory/aggregator sites, checks the candidate site really is that mosque
(its postcode or name appears on the page), looks for a prayer-timetable link,
and adds it as a source. The daily collector then validates it; sources that
never yield valid jamaat times switch themselves off after two weeks.
Run it weekly (the workflow does) — it works through the list gradually.
"""
import argparse
import os
import re
from urllib.parse import urljoin, urlparse

import requests

from . import fetch
from .extract import TIMETABLE_WORDS, page_text
from .store import Store

BRAVE_KEY = os.environ.get("BRAVE_SEARCH_KEY", "")

# Directories, social networks and listing sites — never a mosque's own timetable.
SKIP_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com", "linkedin.com",
    "google.com", "maps.apple.com", "wikipedia.org", "zabihah.com", "praysalat.com", "masjidway.com",
    "mosquedirectory.co.uk", "esalah.com", "salahtimes.com", "salaahtimes.co.uk", "ghh.org.uk",
    "prayersconnect.com", "mosquenearme.net", "nearestmosque.com", "cylex-uk.co.uk", "yell.com",
    "tripadvisor.co.uk", "charitycommission.gov.uk", "register-of-charities.charitycommission.gov.uk",
    "muslimsinbritain.org", "prayer-times.co.uk", "mosquefinder.co.uk", "mylocalmasjid.com",
    "takbeertime.com", "islamicfinder.org", "muslimpro.com", "thebestof.co.uk", "locaji.co.uk",
    "findglocal.com", "wheree.com", "jamaat360.com", "salatomatic.com", "mosquepay.co.uk",
    "launchgood.com", "justgiving.com", "companieshouse.gov.uk", "find-and-update.company-information.service.gov.uk",
}
# Mosque screen systems: these pages ARE the mosque's own live timetable.
SCREEN_SYSTEMS = {"masjidbox.com", "mawaqit.net", "mymasjid.uk"}


def domain(url: str) -> str:
    d = urlparse(url).netloc.lower()
    return d[4:] if d.startswith("www.") else d


def search(q: str) -> list[str]:
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": q, "country": "gb", "count": 10},
                     headers={"X-Subscription-Token": BRAVE_KEY, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("web", {}).get("results", [])]


def is_this_mosque(text: str, m: dict) -> bool:
    flat = re.sub(r"\s", "", text.upper())
    if m["postcode"].replace(" ", "").upper() in flat:
        return True
    words = [w for w in re.findall(r"[a-z]{4,}", m["name"].lower())
             if w not in {"masjid", "mosque", "jamia", "jame", "islamic", "centre", "center", "trust", "association", "community"}]
    t = text.lower()
    return bool(words) and sum(w in t for w in words) / len(words) >= 0.6 and m["town"].lower() in t


def find_source(m: dict) -> tuple[str | None, str | None]:
    """Returns (website, timetable_url)."""
    q = f"\"{m['name']}\" {m['town']} {m['postcode']} prayer timetable"
    for url in search(q):
        d = domain(url)
        if any(d == s or d.endswith("." + s) for s in SKIP_DOMAINS):
            continue
        if any(d == s or d.endswith("." + s) for s in SCREEN_SYSTEMS):
            try:
                body, _, _ = fetch.get(url)
            except (fetch.Blocked, fetch.FetchError):
                continue
            text, _ = page_text(body, url)
            if is_this_mosque(text, m) or all(w in text.lower() for w in m["name"].lower().split()[:2]):
                return None, url
            continue
        try:
            body, ctype, _ = fetch.get(url)
        except (fetch.Blocked, fetch.FetchError):
            continue
        if "html" not in ctype:
            continue
        text, files = page_text(body, url)
        if not is_this_mosque(text, m):
            continue
        site = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
        if files:
            return site, files[0]
        # look for a link to a prayer-times page on the same site
        for href in re.findall(r'href="([^"]+)"', body.decode("utf-8", "ignore")):
            full = urljoin(url, href)
            if domain(full) == d and TIMETABLE_WORDS.search(href):
                return site, full
        return site, url
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="mosques to try this run")
    args = ap.parse_args()
    if not BRAVE_KEY:
        raise SystemExit("Set BRAVE_SEARCH_KEY to run discovery")
    store = Store()
    mosques = store._req("GET", "mosques?select=id,name,town,postcode,website&active=is.true&order=tier.desc,id",
                         headers={"Range": "0-4999"})
    have = {r["mosque_id"] for r in store._req("GET", "sources?select=mosque_id", headers={"Range": "0-9999"})}
    tried = 0
    for m in mosques:
        if m["id"] in have or tried >= args.limit:
            continue
        tried += 1
        try:
            site, tt = find_source(m)
        except Exception as e:
            print(f"{m['name']}: search failed — {e}")
            continue
        if tt:
            store.upsert("sources", [{"mosque_id": m["id"], "url": tt}], "mosque_id,url")
            if site and not m.get("website"):
                store._req("PATCH", f"mosques?id=eq.{m['id']}", json={"website": site}, headers={"Prefer": "return=minimal"})
            print(f"{m['name']}: added {tt}")
        else:
            print(f"{m['name']}: nothing found")


if __name__ == "__main__":
    main()
