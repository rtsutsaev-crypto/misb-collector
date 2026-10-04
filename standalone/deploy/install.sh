#!/bin/bash
# Установка отдельной версии монитора МИСБ на чистый Ubuntu 24.04 (запуск от root):
#   git clone https://github.com/<ваш-аккаунт>/misb-collector.git /opt/misb
#   bash /opt/misb/standalone/deploy/install.sh [путь к снимку данных: папка или .zip]
# Повторный запуск безопасен: обновляет службы, базу и /etc/misb/env не трогает.
set -euo pipefail
REPO=/opt/misb
SNAP="${1:-}"
[ "$(id -u)" = 0 ] || { echo "запускайте от root"; exit 1; }
[ -f "$REPO/standalone/misb/server.py" ] || { echo "репозиторий должен лежать в $REPO"; exit 1; }

apt-get update -q
apt-get install -y -q python3 curl unzip ca-certificates debian-keyring debian-archive-keyring apt-transport-https gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

id misb >/dev/null 2>&1 || useradd --system --home /var/lib/misb --shell /usr/sbin/nologin misb
install -d -o misb -g misb /var/lib/misb /var/lib/misb/work /var/lib/misb/backup /var/log/misb
install -d -m 750 /etc/misb
if [ ! -f /etc/misb/env ]; then
  install -m 600 "$REPO/standalone/deploy/env.example" /etc/misb/env
  PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
  sed -i "s|^MISB_PASSWORD=$|MISB_PASSWORD=$PW|" /etc/misb/env
  echo "Создан /etc/misb/env. Пароль сайта: $PW (пользователь misb). Впишите ключи: nano /etc/misb/env"
fi
chgrp misb /etc/misb /etc/misb/env; chmod 640 /etc/misb/env
chown -R root:root "$REPO"; chmod -R a+rX "$REPO"

set -a; . /etc/misb/env; set +a
if [ -n "$SNAP" ]; then
  if [ ! -f "$MISB_DB" ] || [ "${FORCE_IMPORT:-}" = 1 ]; then
    D="$SNAP"
    if [ "${SNAP##*.}" = zip ]; then D=$(mktemp -d); unzip -q "$SNAP" -d "$D"; [ -d "$D/snapshot" ] && D="$D/snapshot"; fi
    (cd "$REPO/standalone" && sudo -u misb python3 -m misb.snapshot import "$D" --db "$MISB_DB")
  else
    echo "База $MISB_DB уже есть — снимок не загружаю (FORCE_IMPORT=1 — загрузить поверх)"
  fi
fi

install -m 644 "$REPO"/standalone/deploy/misb-server.service "$REPO"/standalone/deploy/misb-collect@.service \
  "$REPO"/standalone/deploy/misb-collect-full.timer "$REPO"/standalone/deploy/misb-collect-light.timer /etc/systemd/system/
install -m 644 "$REPO/standalone/deploy/logrotate-misb" /etc/logrotate.d/misb
echo "30 3 * * * misb $REPO/standalone/deploy/backup.sh" > /etc/cron.d/misb-backup
systemctl daemon-reload
systemctl enable --now misb-server.service misb-collect-full.timer
echo
echo "Готово. Сайт слушает 127.0.0.1:${MISB_PORT:-8080}."
echo "1) Впишите ключи в /etc/misb/env и: systemctl restart misb-server"
echo "2) HTTPS: впишите домен в $REPO/standalone/deploy/Caddyfile, cp его в /etc/caddy/Caddyfile, systemctl reload caddy"
echo "3) Пробный сбор: systemctl start misb-collect@full --no-block; journalctl -fu misb-collect@full; tail -f /var/log/misb/run-full.log"
echo "Облегчённые запуски 12:28 и 18:28 (дополнительные расходы): systemctl enable --now misb-collect-light.timer"
