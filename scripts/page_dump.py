"""Save the readable text (plain and browser-rendered) of the URLs in scripts/dump_urls.txt to exports/pages/."""
import os, re
from collector import fetch
from collector.extract import page_text

os.makedirs("exports/pages", exist_ok=True)
for url in [u.strip() for u in open("scripts/dump_urls.txt") if u.strip()]:
    name = re.sub(r"[^a-z0-9]+", "_", url.lower())[:80]
    out = [f"URL: {url}"]
    for label, fn in (("plain", lambda: fetch.get(url)[0]), ("rendered", lambda: fetch.render(url))):
        try:
            html = fn()
            text, _ = page_text(html, url)
            hrefs = sorted(set(re.findall(r'href=["\']([^"\'#]+)', html.decode("utf-8", "ignore"))))
            out += [f"\n===== {label} =====", text[:20000], "\n--- links ---", "\n".join(hrefs[:300])]
        except Exception as e:
            out.append(f"\n===== {label}: failed — {e} =====")
    open(f"exports/pages/{name}.txt", "w").write("\n".join(out))
    print(url, "saved")
