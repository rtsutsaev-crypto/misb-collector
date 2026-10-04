#!/bin/sh
# Ежедневная копия базы (cron: 30 3 * * * misb /opt/misb/standalone/deploy/backup.sh). Хранит 14 последних.
set -e
. /etc/misb/env
D=/var/lib/misb/backup; mkdir -p "$D"
python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" "$MISB_DB" "$D/misb-$(date +%F).sqlite3"
gzip -f "$D/misb-$(date +%F).sqlite3"
ls -1t "$D"/misb-*.sqlite3.gz | tail -n +15 | xargs -r rm -f
