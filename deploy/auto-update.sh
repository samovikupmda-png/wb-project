#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

cd /var/www/wb-project
git config --global --add safe.directory /var/www/wb-project 2>/dev/null

OLD=$(git rev-parse HEAD 2>/dev/null)
git pull origin claude/project-management-analysis-jKH5e -q >> /tmp/wb-update.log 2>&1
NEW=$(git rev-parse HEAD 2>/dev/null)

if [ "$OLD" != "$NEW" ]; then
    echo "$(date): Updated $OLD -> $NEW" >> /tmp/wb-update.log
    # Install any new Python packages
    /var/www/wb-project/venv/bin/pip install -r /var/www/wb-project/requirements.txt -q >> /tmp/wb-update.log 2>&1
    chown -R www-data:www-data /var/www/wb-project
    systemctl restart wb-project
fi
