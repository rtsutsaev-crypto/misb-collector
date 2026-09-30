#!/usr/bin/env python3
"""Paste block for the Russian server: a daily collection of the Moscow-region market (магазин МО) that accumulates new leads, and a one-line export.

The trades there stay open only 2-4 days, so a weekly run would miss most. The block installs (a) etp_search.py, the dictionary and the Russian root certificate, (b) run_mosreg.sh
(full scan of the open trades, only leads not seen before are appended to mosreg_pending.jsonl and their ids to mosreg_seen.txt), (c) a cron entry: every day at 07:00.
Export (once a week): print mosreg_pending.jsonl into the chat, then move the file away. Remove the schedule with:  crontab -r
    python3 make_ru_cron_block.py OUT_DIR
"""
import base64, gzip, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
out = sys.argv[1] if len(sys.argv) > 1 else HERE
def b64(t):
    g = base64.b64encode(gzip.compress(t.encode(), 9, mtime=0)).decode(); return "\n".join(g[i:i + 100] for i in range(0, len(g), 100))
code = open(os.path.join(HERE, "etp_search.py"), encoding="utf-8").read()
SEED = ['MOSREG-3758477', 'MOSREG-3758401', 'MOSREG-3758402', 'MOSREG-3758368', 'MOSREG-3758359', 'MOSREG-3758352', 'MOSREG-3758318', 'MOSREG-3758293', 'MOSREG-3758300', 'MOSREG-3758302', 'MOSREG-3758226', 'MOSREG-3758228', 'MOSREG-3758144', 'MOSREG-3758149', 'MOSREG-3758133', 'MOSREG-3758069', 'MOSREG-3758004', 'MOSREG-3757917', 'MOSREG-3757811', 'MOSREG-3757776', 'MOSREG-3757683', 'MOSREG-3757534', 'MOSREG-3756886', 'MOSREG-3756823', 'MOSREG-3750969', 'MOSREG-3750782']  # ids already written to the base (leadsets/run-20260930-0100-mosreg)
terms = open(os.path.join(HERE, "..", "ru_pilot", "dictionary_terms.json"), encoding="utf-8").read()
pem = open(os.path.join(HERE, "..", "ru_pilot", "russian_trusted_root_ca.pem"), encoding="ascii").read()
RUN = r'''#!/bin/bash
cd "$HOME/pilot_new" || exit 1
touch mosreg_seen.txt mosreg_pending.jsonl
python3 etp_search.py --terms dictionary_terms.json --sources mosreg --cafile russian_root.pem --pause 1.5 --known mosreg_seen.txt --out mosreg_today.json >> cron.log 2>&1
python3 - <<'PY_EOF' >> cron.log 2>&1
import json, datetime
j = json.load(open('mosreg_today.json'))
with open('mosreg_pending.jsonl', 'a') as f, open('mosreg_seen.txt', 'a') as s:
    for l in j['leads']:
        f.write(json.dumps(l, ensure_ascii=False) + '\n')
        s.write(l['id'] + '\n')
print(datetime.datetime.now().isoformat(timespec='minutes'), 'новых', len(j['leads']))
PY_EOF
'''
block = ("command -v python3 >/dev/null || (apt-get update && apt-get install -y python3)\n"
 "command -v crontab >/dev/null || (apt-get update && apt-get install -y cron)\n"
 "mkdir -p ~/pilot_new && cd ~/pilot_new\n"
 "base64 -d <<'PILOT_EOF' | gunzip > etp_search.py\n" + b64(code) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > dictionary_terms.json\n" + b64(terms) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > russian_root.pem\n" + b64(pem) + "\nPILOT_EOF\n"
 "base64 -d <<'PILOT_EOF' | gunzip > run_mosreg.sh\n" + b64(RUN) + "\nPILOT_EOF\n"
 "chmod +x run_mosreg.sh\n"
 "touch mosreg_seen.txt; for i in " + " ".join(SEED) + "; do grep -qx $i mosreg_seen.txt || echo $i >> mosreg_seen.txt; done\n"
 "(crontab -l 2>/dev/null | grep -v run_mosreg.sh; echo \"0 7 * * * bash $HOME/pilot_new/run_mosreg.sh\") | crontab -\n"
 "echo '=== первый запуск сейчас (около 3 минут) ==='\n"
 "bash run_mosreg.sh; tail -3 cron.log; wc -l mosreg_pending.jsonl\n"
 "echo '=== расписание: ==='; crontab -l | grep run_mosreg\n"
 "echo '=== ГОТОВО ==='\n")
open(os.path.join(out, "paste_ru_mosreg_cron.txt"), "w", encoding="utf-8").write(block)
open(os.path.join(out, "ru_export_mosreg.txt"), "w", encoding="utf-8").write(
    "cd ~/pilot_new && wc -l mosreg_pending.jsonl && cat mosreg_pending.jsonl\n")
print(len(block), "байт")
