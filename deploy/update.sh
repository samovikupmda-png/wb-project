#!/bin/bash
# Обновление приложения на сервере (запускать от root)
set -e

APP_DIR="/var/www/wb-tools"
BRANCH="claude/connect-aez-integration-jHmt7"

echo "=== Получение обновлений ==="
cd "$APP_DIR"
git fetch origin
git pull origin "$BRANCH"

echo "=== Обновление зависимостей ==="
"$APP_DIR/venv/bin/pip" install -r requirements.txt -q

echo "=== Перезапуск сервиса ==="
systemctl restart wb-tools

echo "Обновление завершено!"
systemctl status wb-tools --no-pager
