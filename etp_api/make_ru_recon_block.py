#!/usr/bin/env python3
"""Paste block for the Russian server: find how the sites that the cloud could not read load their lists (recon_sites.cjs).

Targets are the sites for which the cloud gave an empty page, a captcha or no answer (probe and recon of 29.09.2026) but the Russian server saw a list-like page.
The block installs node + Playwright + Chromium (as in ru_pilot round 2), runs recon_sites.cjs on every target one at a time (robots.txt first, pause between
pages, TLS on, no login, captcha not bypassed) and zips the result: ~/pilot_new/recon_ru.zip.
    python3 make_ru_recon_block.py OUT_DIR
"""
import base64, gzip, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
out = sys.argv[1] if len(sys.argv) > 1 else HERE
TARGETS = [
    "https://agregatoreat.ru",            # ЕАТ «Берёзка»: captcha for the cloud, 17 rows in the Russian browser
    "https://zakupki.tochka.com",         # Точка Закупки: cloud no answer; needs the Russian root CA
    "https://market.mosreg.ru",           # Электронный магазин МО: cloud no answer; "Образовательные услуги 507 закупок"
    "https://torgi.egov66.ru",            # витрина Свердловской области
    "https://new.etpgpb.ru",              # ЭТП Газпромбанка
    "https://onlinecontract.ru",
    "https://supply.evraz.com",
    "https://estp.ru",
    "https://etp-region.ru",
    "https://zakupki223.eltorg.org",
    "https://torgi223.ru",                # Yandex SmartCaptcha appears in the cloud: the block reports it, does not solve it
    "https://www.etp-avtodor.ru",         # certificate: needs the Russian root CA
    "https://etp.setonline.ru",
    "https://portal-zakupok.tatar",
]
def b64(t):
    g = base64.b64encode(gzip.compress(t.encode(), 9, mtime=0)).decode(); return "\n".join(g[i:i + 100] for i in range(0, len(g), 100))
cjs = open(os.path.join(HERE, "recon_sites.cjs"), encoding="utf-8").read()
pem = open(os.path.join(HERE, "..", "ru_pilot", "russian_trusted_root_ca.pem"), encoding="ascii").read()
FP = "D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31"
block = ("command -v python3 >/dev/null || (apt-get update && apt-get install -y python3)\n"
 "mkdir -p ~/pilot_new && cd ~/pilot_new\n"
 "base64 -d <<'PILOT_EOF' | gunzip > recon_sites.cjs\n" + b64(cjs) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > russian_root.pem\n" + b64(pem) + "\nPILOT_EOF\n"
 "echo '=== 1/3: браузер (node, Playwright 1.49.1, Chromium) ==='\n"
 "apt-get update -qq && apt-get install -y -qq nodejs npm ca-certificates\n"
 "(cd ~ && npm install playwright@1.49.1 && npx playwright install --with-deps chromium)\n"
 "echo '=== 2/3: корень Минцифры для сайтов с российской цепочкой (отпечаток проверяется; TLS остаётся включённым) ==='\n"
 "if openssl x509 -in russian_root.pem -noout -fingerprint -sha256 | grep -qF '" + FP + "'; then\n"
 "  cp russian_root.pem /usr/local/share/ca-certificates/russian_root.crt && update-ca-certificates >/dev/null 2>&1\n"
 "  export NODE_EXTRA_CA_CERTS=$HOME/pilot_new/russian_root.pem\n"
 "  mkdir -p ~/.pki/nssdb && apt-get install -y -qq libnss3-tools >/dev/null 2>&1 && { [ -f ~/.pki/nssdb/cert9.db ] || certutil -N -d sql:$HOME/.pki/nssdb --empty-password; certutil -A -d sql:$HOME/.pki/nssdb -n russian-root -t 'C,,' -i russian_root.pem; }\n"
 "else echo 'ОТПЕЧАТОК НЕ СОВПАЛ, корень не добавлен'; fi\n"
 "echo '=== 3/3: разведка ' " + str(len(TARGETS)) + "' сайтов (около 15-25 минут) ==='\n"
 "export PLAYWRIGHT_MODULE=$HOME/node_modules/playwright\n"
 "node recon_sites.cjs " + " ".join(TARGETS) + " --follow 4 --pause 3 --out recon_ru.json > recon_ru.txt 2>&1\n"
 "python3 -m zipfile -c recon_ru.zip recon_ru.json recon_ru.txt\n"
 "echo '=== ГОТОВО. Дальше: clear, затем  grep -E \"^########|LIST|GATE|ERR|robots\" recon_ru.txt | cut -c1-220 ; ls -la ~/pilot_new/recon_ru.zip ==='\n")
open(os.path.join(out, "paste_ru_recon.txt"), "w", encoding="utf-8").write(block)
print(len(TARGETS), "сайтов;", len(block), "байт")
