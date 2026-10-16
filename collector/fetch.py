"""Polite fetching: obeys robots.txt, identifies itself, rate-limits per website."""
import hashlib
import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from . import config


class Blocked(Exception):
    """The site forbids automated access (robots.txt) — never fetch it."""


class FetchError(Exception):
    pass


_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en-GB,en;q=0.8"})
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}
_lock = threading.Lock()          # the collector checks several websites at once


def _robots_for(url: str):
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = _session.get(root + "/robots.txt", timeout=config.REQUEST_TIMEOUT)
            if r.status_code in (401, 403):
                rp.disallow_all = True           # access to robots.txt itself refused: treat as "no"
            elif r.status_code >= 400:
                rp.allow_all = True              # no robots.txt: allowed
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None                            # could not reach — decide per request below
        _robots[root] = rp
    return _robots[root]


def allowed(url: str) -> bool:
    rp = _robots_for(url)
    if rp is None:
        return True
    return rp.can_fetch(config.USER_AGENT, url)


def _wait_turn(url: str):
    """At most one request every PER_HOST_DELAY seconds to any one website."""
    host = urlparse(url).netloc
    with _lock:
        now = time.time()
        slot = max(now, _last_hit.get(host, 0) + config.PER_HOST_DELAY)
        _last_hit[host] = slot
    if slot > now:
        time.sleep(slot - now)


def get(url: str) -> tuple[bytes, str, str]:
    """Return (content, content_type, sha256). Raises Blocked or FetchError."""
    if not allowed(url):
        raise Blocked("robots.txt disallows automated access")
    _wait_turn(url)
    try:
        r = _session.get(url, timeout=config.REQUEST_TIMEOUT, stream=True)
    except requests.RequestException as e:
        raise FetchError(str(e)[:200])
    if r.status_code in (401, 403, 451):
        raise Blocked(f"site refused access (HTTP {r.status_code})")
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    chunks, size = [], 0
    for chunk in r.iter_content(64 * 1024):
        size += len(chunk)
        if size > config.MAX_BYTES:
            raise FetchError("file too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    return body, ctype, hashlib.sha256(body).hexdigest()
