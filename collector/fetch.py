"""Polite fetching: obeys robots.txt, identifies itself, rate-limits per website."""
import hashlib
import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from . import config


class Blocked(Exception):
    """The site forbids automated access (robots.txt or a refusal) — not fetched again until the weekly recheck."""


class FetchError(Exception):
    pass


_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en-GB,en;q=0.8"})
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}
final_url: dict[str, str] = {}    # url asked for → address it ended up at after redirects (site moved)
_lock = threading.Lock()          # the collector checks several websites at once


def _robots_for(url: str):
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = _session.get(root + "/robots.txt", timeout=config.REQUEST_TIMEOUT)
            if r.status_code >= 500:
                rp = None                        # server trouble: decide per request (never a lasting "no")
            elif r.status_code >= 400:
                # RFC 9309 §2.3.1.3: robots.txt missing or unavailable (any 4xx) means no rules apply.
                # Only a robots.txt that actually disallows us counts as the site saying "no".
                rp.allow_all = True
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
    final_url[url] = r.url or url
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


# ───────────── pages that only show times after JavaScript runs ─────────────
_tls = threading.local()      # one browser per worker thread (Playwright objects aren't shared across threads)


def _browser():
    b = getattr(_tls, "browser", None)
    if b is None:
        from playwright.sync_api import sync_playwright
        _tls.pw = sync_playwright().start()
        b = _tls.browser = _tls.pw.chromium.launch()
    return b


# True once the page text shows at least five clock times (e.g. "5:18am", "13:00").
TIMES_SHOWN_JS = r"""() => ((document.body && document.body.innerText) || '').match(/(?<!\d)\d{1,2}[:.]\d{2}(?!\d)/g)?.length >= 5"""


def render(url: str) -> bytes:
    """The page's HTML after a headless browser has run its JavaScript.
    Same rules as get(): robots.txt, one visit every few seconds per site, refusals respected."""
    if not allowed(url):
        raise Blocked("robots.txt disallows automated access")
    _wait_turn(url)
    try:
        browser = _browser()
    except Exception as e:                      # Playwright/Chromium not installed
        raise FetchError(f"browser not available ({str(e)[:80]})")
    ctx = browser.new_context(user_agent=config.USER_AGENT, locale="en-GB", timezone_id="Europe/London")
    page = ctx.new_page()
    # no images, video or fonts: faster and lighter on the mosque's server
    page.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font")
               else r.continue_())
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if resp is not None and resp.status in (401, 403, 451):
            raise Blocked(f"site refused access (HTTP {resp.status})")
        if resp is not None and resp.status >= 400:
            raise FetchError(f"HTTP {resp.status}")
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass                                # some pages never go quiet (live clocks); use what has loaded
        page.wait_for_timeout(1500)
        try:
            # Some sites fill in the timetable well after the page goes quiet: give them a little longer
            # when no prayer times are showing yet.
            page.wait_for_function(TIMES_SHOWN_JS, timeout=8000)
        except Exception:
            pass
        html = page.content()
    except (Blocked, FetchError):
        raise
    except Exception as e:
        raise FetchError(f"browser could not load the page ({str(e)[:100]})")
    finally:
        ctx.close()
    return html.encode("utf-8")
