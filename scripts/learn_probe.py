"""Read-only probe: for the URLs in scripts/learn_probe.txt, do what recipe learning does (same fetch, same
threads) but with no AI and no saving, and write what each step saw to exports/pages/learn_probe.txt."""
import os, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from collector import config, fetch
from collector.extract import has_times, page_text
from collector.learn import Learner

path = "scripts/learn_probe.txt"
urls = [u.strip() for u in open(path) if u.strip()] if os.path.exists(path) else []
os.makedirs("exports/pages", exist_ok=True)
today = datetime.now(ZoneInfo(config.TIMEZONE)).date()


class NoAI:
    calls = 0
    def ask(self, *a, **k):
        return {"kind": "none", "reason": "probe: stopped before AI"}
    def read(self, *a, **k):
        return {"has_jamaat": False, "days": {}}


lock, out = threading.Lock(), []


def probe(url):
    lines = [f"## {url}"]
    for label, fn in (("get", lambda: fetch.get(url)), ("render", lambda: (fetch.render(url), "text/html", ""))):
        try:
            body, ctype, _ = fn()
            text, _ = page_text(body, url)
            lines.append(f"{label}: {len(body)} bytes, {ctype}, has_times={has_times(text)}; text: {text[:300]!r}")
            if not has_times(text):
                lines.append(f"{label} raw start: {body[:600]!r}")
        except Exception as e:
            lines.append(f"{label}: failed — {type(e).__name__}: {e}")
    try:
        m = {"id": 0, "name": url, "town": "UK", "lat": 52.48, "lng": -1.89}
        r, msg, days, tt = Learner(NoAI(), today).learn(m, url)
        lines.append(f"learn (no AI): {r.get('kind')} — {msg}")
    except Exception as e:
        lines.append(f"learn: failed — {type(e).__name__}: {e}")
    with lock:
        out.append("\n".join(lines))


with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
    list(pool.map(probe, urls))
open("exports/pages/learn_probe.txt", "w").write("\n\n".join(out))
print(len(out), "probed")
