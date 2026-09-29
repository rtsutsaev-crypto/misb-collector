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

# second block: retry the pages that failed on the Russian root certificate, read pages without a list with a browser
import hashlib
import ssl
FP = "D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31"
pem = open(os.path.join(HERE, "russian_trusted_root_ca.pem"), encoding="ascii").read()
digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
assert ":".join(digest[i:i + 2] for i in range(0, 64, 2)) == FP, "root certificate does not match the expected fingerprint"
cjs = open(os.path.join(HERE, "..", "browser_lists", "render_list.cjs"), encoding="utf-8").read()


def b64lines(text):
    g = base64.b64encode(gzip.compress(text.encode("utf-8"), 9, mtime=0)).decode("ascii")
    return "\n".join(g[i:i + 100] for i in range(0, len(g), 100))


block2 = ("cd ~/pilot\n"
          "base64 -d <<'PILOT_EOF' | gunzip > pilot.py\n" + b64lines(one) + "\nPILOT_EOF\n"
          "base64 -d <<'PILOT_EOF' | gunzip > russian_root.pem\n" + b64lines(pem) + "\nPILOT_EOF\n"
          "echo '=== 1/3: повтор адресов с ошибкой сертификата (корень Минцифры добавлен только для скрипта, проверка TLS включена) ==='\n"
          "if openssl x509 -in russian_root.pem -noout -fingerprint -sha256 | grep -qF '" + FP + "'; then\n"
          "  python3 pilot.py --from-result result.json --classes error --error-contains CERTIFICATE_VERIFY --cafile russian_root.pem --out result_ca.json --report report_ca.md\n"
          "else echo 'ОТПЕЧАТОК СЕРТИФИКАТА НЕ СОВПАЛ, шаг пропущен'; fi\n"
          "python3 pilot.py --peek https://synapsenet.ru/search/populyarnie-zaprosi/provedenie-seminarov > peek_synapsenet.txt\n"
          "echo '=== 2/3: установка браузера (node, Playwright 1.49.1, Chromium) ==='\n"
          "apt-get update -qq && apt-get install -y -qq nodejs npm\n"
          "mkdir -p ~/browser_lists && (cd ~ && npm install playwright@1.49.1 && npx playwright install --with-deps chromium)\n"
          "base64 -d <<'PILOT_EOF' | gunzip > ~/browser_lists/render_list.cjs\n" + b64lines(cjs) + "\nPILOT_EOF\n"
          "echo '=== 3/3: чтение браузером страниц без списка ==='\n"
          "export PLAYWRIGHT_MODULE=$HOME/node_modules/playwright\n"
          "python3 pilot.py --browser --from-result result.json --classes shell --out result_browser.json --report report_browser.md\n"
          "[ -f result_ca.json ] && python3 pilot.py --browser --from-result result_ca.json --classes shell --out result_ca_browser.json --report report_ca_browser.md\n"
          "echo '=== ГОТОВО. Дальше: clear, затем  python3 pilot.py --summary result_ca.json result_browser.json result_ca_browser.json ; cat peek_synapsenet.txt ==='\n")
open(os.path.join(HERE, "paste_round2.txt"), "w", encoding="utf-8").write(block2)
print(f"paste_round2.txt {len(block2)} bytes")

# third block: what the JSON requests of the sites that are open only from a Russian address look like (search by one word, as a visitor)
recon = open(os.path.join(HERE, "recon.cjs"), encoding="utf-8").read()
block3 = ("cd ~/pilot\n"
          "base64 -d <<'PILOT_EOF' | gunzip > pilot.py\n" + b64lines(one) + "\nPILOT_EOF\n"
          "base64 -d <<'PILOT_EOF' | gunzip > recon.cjs\n" + b64lines(recon) + "\nPILOT_EOF\n"
          "export PLAYWRIGHT_MODULE=$HOME/node_modules/playwright\n"
          "(\n"
          "python3 pilot.py --getjson 'https://bidzaar.com/api/process/light/procedures/available?paging.page=1&paging.size=25&sorting.key=publishDate&sorting.direction=desc'\n"
          "echo; echo '##### Bidzaar'; node recon.cjs https://bidzaar.com/app/requests/public/buy обучение\n"
          "echo; echo '##### Свердловская область'; node recon.cjs https://torgi.egov66.ru обучение\n"
          "echo; echo '##### Московская область'; node recon.cjs https://market.mosreg.ru обучение\n"
          ") > recon_out.txt 2>&1\n"
          "echo '=== ГОТОВО. Дальше: clear, затем  cat recon_out.txt ==='\n")
open(os.path.join(HERE, "paste_round3.txt"), "w", encoding="utf-8").write(block3)
print(f"paste_round3.txt {len(block3)} bytes")
