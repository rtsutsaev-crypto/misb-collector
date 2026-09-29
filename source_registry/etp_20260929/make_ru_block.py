#!/usr/bin/env python3
"""Paste block for the Russian server: the new sources (plan 5.9) that the cloud could not read or read without a list.

Takes probe_results.json (cloud check 29.09.2026) and ../../ru_pilot/probe_ru.py. Left out: robots.txt refusals (a rule of the site,
not of the address) and 404. Stages: probe -> retry certificate errors with the Russian root CA (fingerprint checked) -> browser for
pages that open without a list. Output: ~/pilot_new/result*.json, report*.md.   python3 make_ru_block.py OUT_DIR
"""
import base64, gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); PILOT = os.path.join(HERE, "..", "..", "ru_pilot")
out = sys.argv[1] if len(sys.argv) > 1 else HERE
res = json.load(open(os.path.join(HERE, "probe_results.json"), encoding="utf-8"))
plan = {a["urls"][0] for a in json.load(open(os.path.join(HERE, "added_to_plan.json"), encoding="utf-8"))["added"]}
urls = [{"name": r["name"], "url": r["url"], "kinds": ["etp20260929"],
         "cloud": {"status": r["status"], "gate": r["gate"], "robots": r["robots"], "note": r["cls"]}}
        for r in res if r["url"] in plan and r["cls"] not in ("robots", "http404")]
src = open(os.path.join(PILOT, "probe_ru.py"), encoding="utf-8").read()
import zlib
blob = base64.b64encode(zlib.compress(json.dumps(urls, ensure_ascii=False, separators=(",", ":")).encode(), 9)).decode()
assert src.count('EMBEDDED_URLS = ""') == 1
one = src.replace('EMBEDDED_URLS = ""', f'EMBEDDED_URLS = "{blob}"')
pem = open(os.path.join(PILOT, "russian_trusted_root_ca.pem"), encoding="ascii").read()
cjs = open(os.path.join(PILOT, "..", "browser_lists", "render_list.cjs"), encoding="utf-8").read()
FP = "D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31"
def b64(t):
    g = base64.b64encode(gzip.compress(t.encode(), 9, mtime=0)).decode(); return "\n".join(g[i:i+100] for i in range(0, len(g), 100))
block = ("command -v python3 >/dev/null || (apt-get update && apt-get install -y python3)\n"
 "mkdir -p ~/pilot_new && cd ~/pilot_new\n"
 "base64 -d <<'PILOT_EOF' | gunzip > pilot.py\n" + b64(one) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > russian_root.pem\n" + b64(pem) + "\nPILOT_EOF\n"
 "echo '=== 1/3: чтение адресов ==='\n"
 "python3 pilot.py\n"
 "echo '=== 2/3: повтор с корнем Минцифры (отпечаток проверяется, TLS включён) ==='\n"
 "if openssl x509 -in russian_root.pem -noout -fingerprint -sha256 | grep -qF '" + FP + "'; then\n"
 "  python3 pilot.py --from-result result.json --classes error --error-contains CERTIFICATE_VERIFY --cafile russian_root.pem --out result_ca.json --report report_ca.md\n"
 "else echo 'ОТПЕЧАТОК НЕ СОВПАЛ, шаг пропущен'; fi\n"
 "echo '=== 3/3: браузер для страниц без списка ==='\n"
 "apt-get update -qq && apt-get install -y -qq nodejs npm\n"
 "mkdir -p ~/browser_lists && (cd ~ && npm install playwright@1.49.1 && npx playwright install --with-deps chromium)\n"
 "base64 -d <<'PILOT_EOF' | gunzip > ~/browser_lists/render_list.cjs\n" + b64(cjs) + "\nPILOT_EOF\n"
 "export PLAYWRIGHT_MODULE=$HOME/node_modules/playwright\n"
 "python3 pilot.py --browser --from-result result.json --classes shell --out result_browser.json --report report_browser.md\n"
 "[ -f result_ca.json ] && python3 pilot.py --browser --from-result result_ca.json --classes shell --out result_ca_browser.json --report report_ca_browser.md\n"
 "echo '=== ГОТОВО. Дальше: clear, затем  python3 pilot.py --summary result.json result_ca.json result_browser.json result_ca_browser.json ==='\n")
open(os.path.join(out, "paste_ru_new_sources.txt"), "w", encoding="utf-8").write(block)
print(len(urls), "адресов;", len(block), "байт")
