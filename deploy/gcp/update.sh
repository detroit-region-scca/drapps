#!/bin/sh
# Weekly (drscca-update.timer, Sundays 4:30 am): newest container images, drapps rebuilt on the newest Python base,
# newest GAM and Python libraries for the host jobs, then old images cleared. Debian, Docker and Node packages are
# updated daily by unattended-upgrades instead.
set -e
cd /opt/drscca
docker compose pull --quiet caddy livetiming-sftp livetiming-web
docker compose build --pull --quiet drapps
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
/opt/drscca/venv/bin/pip install --quiet --upgrade pip gam7 -r /opt/drscca/app/requirements.txt
docker compose ps --format '{{.Name}} {{.Status}}'
