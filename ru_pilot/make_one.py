#!/usr/bin/env python3
"""Builds the single-file pilot: probe_ru.py with urls.json embedded (zlib+base64), and a paste block for a server console.

  python3 make_one.py      ->  pilot_one.py, paste_to_server.txt
The paste block installs nothing but python3 (if missing), decodes pilot_one.py from base64+gzip and runs it.
"""
import base64
import gzip
import json
import os
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "probe_ru.py"), encoding="utf-8").read()
urls = json.load(open(os.path.join(HERE, "urls.json"), encoding="utf-8"))
blob = base64.b64encode(zlib.compress(json.dumps(urls, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)).decode("ascii")
marker = 'EMBEDDED_URLS = ""'
assert src.count(marker) == 1
one = src.replace(marker, f'EMBEDDED_URLS = "{blob}"')
open(os.path.join(HERE, "pilot_one.py"), "w", encoding="utf-8").write(one)
gz = base64.b64encode(gzip.compress(one.encode("utf-8"), 9, mtime=0)).decode("ascii")
lines = [gz[i:i + 100] for i in range(0, len(gz), 100)]
block = ("command -v python3 >/dev/null || (apt-get update && apt-get install -y python3)\n"
         "mkdir -p ~/pilot && cd ~/pilot\n"
         "base64 -d <<'PILOT_EOF' | gunzip > pilot.py\n" + "\n".join(lines) + "\nPILOT_EOF\n"
         "python3 pilot.py\n")
open(os.path.join(HERE, "paste_to_server.txt"), "w", encoding="ascii").write(block)
print(f"pilot_one.py {len(one)} bytes, paste block {len(block)} bytes, {len(lines)} lines")

# second block: browser stage for the pages that opened without a list (needs the first run's result.json in ~/pilot)
cjs = open(os.path.join(HERE, "..", "browser_lists", "render_list.cjs"), encoding="utf-8").read()
def b64lines(text):
    g = base64.b64encode(gzip.compress(text.encode("utf-8"), 9, mtime=0)).decode("ascii")
    return "\n".join(g[i:i + 100] for i in range(0, len(g), 100))
block2 = ("cd ~/pilot\n"
          "apt-get update -qq && apt-get install -y -qq nodejs npm\n"
          "mkdir -p ~/browser_lists && (cd ~ && npm install playwright@1.49.1 && npx playwright install --with-deps chromium)\n"
          "base64 -d <<'PILOT_EOF' | gunzip > pilot.py\n" + b64lines(one) + "\nPILOT_EOF\n"
          "base64 -d <<'PILOT_EOF' | gunzip > ~/browser_lists/render_list.cjs\n" + b64lines(cjs) + "\nPILOT_EOF\n"
          "PLAYWRIGHT_MODULE=$HOME/node_modules/playwright python3 pilot.py --browser --from-result result.json --classes shell "
          "--out result_browser.json --report report_browser.md\n")
open(os.path.join(HERE, "paste_browser.txt"), "w", encoding="ascii").write(block2)
print(f"paste_browser.txt {len(block2)} bytes")
