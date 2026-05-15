#!/bin/bash
set -e

REPO="https://github.com/samovikupmda-png/wb-project.git"
BRANCH="claude/project-management-analysis-jKH5e"
APP_DIR="/var/www/wb-project"

echo "==> Обновление системы и установка зависимостей"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx git

echo "==> Клонирование/обновление репозитория"
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
else
    git clone -b "$BRANCH" "$REPO" "$APP_DIR"
fi

echo "==> Настройка виртуального окружения"
cd "$APP_DIR"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "==> Создание папок"
mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/static/uploads"
mkdir -p /var/log/wb-project
chown -R www-data:www-data "$APP_DIR"
chown -R www-data:www-data /var/log/wb-project

echo "==> Настройка systemd-сервиса"
cp "$APP_DIR/deploy/wb-project.service" /etc/systemd/system/wb-project.service
systemctl daemon-reload
systemctl enable wb-project
systemctl restart wb-project

echo "==> Настройка nginx"
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/wb-project
ln -sf /etc/nginx/sites-available/wb-project /etc/nginx/sites-enabled/wb-project
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo ""
echo "✓ Готово! Сайт доступен по IP сервера на порту 80."
systemctl status wb-project --no-pager
