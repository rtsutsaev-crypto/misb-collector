#!/usr/bin/env python3
"""Paste block for the Russian server: run etp_search.py for the sources that only a Russian address reaches (СЭТ and магазин МО).

Installs nothing but python3. The Russian root certificate is checked by fingerprint and passed with --cafile (TLS verification stays on).
Result: ~/pilot_new/ru_leads.json; the block prints the counters and every lead as one compact line, so the output can be pasted into the chat.
    python3 make_ru_collect_block.py OUT_DIR
"""
import base64, gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
out = sys.argv[1] if len(sys.argv) > 1 else HERE
def b64(t):
    g = base64.b64encode(gzip.compress(t.encode(), 9, mtime=0)).decode(); return "\n".join(g[i:i + 100] for i in range(0, len(g), 100))
code = open(os.path.join(HERE, "etp_search.py"), encoding="utf-8").read()
terms = open(os.path.join(HERE, "..", "ru_pilot", "dictionary_terms.json"), encoding="utf-8").read()
pem = open(os.path.join(HERE, "..", "ru_pilot", "russian_trusted_root_ca.pem"), encoding="ascii").read()
FP = "D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31"
block = ("command -v python3 >/dev/null || (apt-get update && apt-get install -y python3)\n"
 "mkdir -p ~/pilot_new && cd ~/pilot_new\n"
 "base64 -d <<'PILOT_EOF' | gunzip > etp_search.py\n" + b64(code) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > dictionary_terms.json\n" + b64(terms) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > russian_root.pem\n" + b64(pem) + "\nPILOT_EOF\n"
 "if openssl x509 -in russian_root.pem -noout -fingerprint -sha256 | grep -qF '" + FP + "'; then\n"
 "  echo '=== СЭТ (поиск по словам) и магазин МО (полный обход 90 страниц), около 5 минут ==='\n"
 "  python3 etp_search.py --terms dictionary_terms.json --sources setonline,mosreg --cafile russian_root.pem --pause 1.5 --out ru_leads.json\n"
 "  python3 - <<'PY_EOF'\n"
 "import json\n"
 "j = json.load(open('ru_leads.json'))\n"
 "print('ЛИДОВ', len(j['leads']))\n"
 "for l in j['leads']:\n"
 "    print(l['source'], '|', l['deadline'] or '-', '|', (l['customer'] or '')[:30], '|', l['title'][:90], '|', l['price'])\n"
 "PY_EOF\n"
 "else echo 'ОТПЕЧАТОК СЕРТИФИКАТА НЕ СОВПАЛ, запуск отменён'; fi\n"
 "echo '=== ГОТОВО ==='\n")
open(os.path.join(out, "paste_ru_collect.txt"), "w", encoding="utf-8").write(block)
print(len(block), "байт")
