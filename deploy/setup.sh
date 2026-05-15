#!/bin/bash
# Запускать от root на сервере Ubuntu 22.04
# Использование: bash setup.sh

set -e

APP_DIR="/var/www/wb-tools"
REPO_URL="https://github.com/samovikupmda-png/wb-project.git"
BRANCH="claude/connect-aez-integration-jHmt7"

echo "=== Установка зависимостей системы ==="
apt-get update -q
apt-get install -y python3 python3-pip python3-venv nginx git

echo "=== Создание директорий ==="
mkdir -p "$APP_DIR"
mkdir -p /var/log/wb-tools

echo "=== Клонирование репозитория ==="
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
else
    git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

echo "=== Создание виртуального окружения ==="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "=== Создание .env файла (если нет) ==="
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
EOF
    echo "Создан .env с новым SECRET_KEY"
fi

echo "=== Настройка прав ==="
chown -R www-data:www-data "$APP_DIR"
chown -R www-data:www-data /var/log/wb-tools
mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/static/uploads"
chown -R www-data:www-data "$APP_DIR/data"
chown -R www-data:www-data "$APP_DIR/static/uploads"

echo "=== Установка systemd-сервиса ==="
cp "$APP_DIR/deploy/wb-tools.service" /etc/systemd/system/wb-tools.service
systemctl daemon-reload
systemctl enable wb-tools
systemctl restart wb-tools

echo "=== Настройка Nginx ==="
cp "$APP_DIR/deploy/wb-tools.nginx" /etc/nginx/sites-available/wb-tools
ln -sf /etc/nginx/sites-available/wb-tools /etc/nginx/sites-enabled/wb-tools
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo ""
echo "=== Готово! ==="
echo "Сайт доступен по адресу: http://178.159.94.48"
echo ""
echo "Полезные команды:"
echo "  systemctl status wb-tools    — статус приложения"
echo "  journalctl -u wb-tools -f    — логи в реальном времени"
echo "  systemctl restart wb-tools   — перезапуск"
