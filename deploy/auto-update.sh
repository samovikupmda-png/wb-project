#!/bin/bash
cd /var/www/wb-project
git config --global --add safe.directory /var/www/wb-project 2>/dev/null
OLD=$(git rev-parse HEAD)
git pull origin claude/project-management-analysis-jKH5e -q
NEW=$(git rev-parse HEAD)
if [ "$OLD" != "$NEW" ]; then
    chown -R www-data:www-data /var/www/wb-project
    systemctl restart wb-project
fi
