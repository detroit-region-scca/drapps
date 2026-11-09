#!/bin/sh
# Deploy drapps to the Google Cloud VM (run on bodhi, from the repo, after committing):
#   deploy/gcp/deploy.sh
# Tests, pushes, then on the VM: pulls the code, copies the compose file, Caddyfile, nginx config, job units and
# scripts into place, rebuilds and restarts drapps, reloads Caddy, and checks the live site.
set -e
cd "$(dirname "$0")/../.."
VM="gcloud compute ssh drapps --project drscca-automation --zone us-central1-a --tunnel-through-iap --quiet --command"

echo "== tests"
OFFICIALS_HOST=/nonexistent PYTHONPATH=. venv/bin/pytest -q tests | tail -1

echo "== push"
if [ -n "$(git status --porcelain)" ]; then echo "Uncommitted changes: commit first."; exit 1; fi
git push -q origin HEAD


echo "== deploy on the VM ($(git log -1 --format='%h %s'))"
$VM 'set -e; cd /opt/drscca/app && sudo git pull -q --ff-only
cd /opt/drscca
for f in docker-compose.yml Caddyfile livetiming-nginx.conf; do sudo cp app/deploy/gcp/$f $f; done
sudo install -m 755 app/deploy/gcp/qbo-mcp.sh /opt/drscca/qbo-mcp.sh
# QuickBooks swaps its token on use and saves the new one with a temp file + rename, so the token needs a folder
# drapps can write (the secrets folder belongs to root): a token it cannot save goes stale and QuickBooks refuses it.
sudo install -d -o drapps -g drapps -m 700 data/secrets/qbo
[ -f data/secrets/qbo.env ] && [ ! -f data/secrets/qbo/qbo.env ] && sudo mv data/secrets/qbo.env data/secrets/qbo/qbo.env
sudo install -m 644 app/deploy/gcp/livetiming-www/*.html data/livetiming/www/
sudo cp app/deploy/gcp/systemd/* /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl restart drscca-officials.path     # picks up new watched files
sudo systemctl enable --now drscca-update.timer drscca-secrets-backup.timer drscca-site-backup.timer drscca-groups.path >/dev/null 2>&1
command -v fail2ban-client >/dev/null || sudo apt-get install -y -qq fail2ban >/dev/null 2>&1
sudo install -m 644 app/deploy/gcp/fail2ban/livetiming-sftp.local /etc/fail2ban/jail.d/livetiming-sftp.local && sudo systemctl reload-or-restart fail2ban
sudo install -m 644 app/deploy/gcp/apt/52drscca-upgrades /etc/apt/apt.conf.d/52drscca-upgrades
[ "$(timedatectl show -p Timezone --value)" = America/Detroit ] || sudo timedatectl set-timezone America/Detroit
sudo docker compose up -d --build 2>&1 | tail -1
sudo docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null || true
sudo docker image prune -f >/dev/null' 2>&1 | grep -v -i -e numpy -e "tunnel" -e "^WARNING" -e "please see" -e "^$"

echo "== check"
for i in 1 2 3 4 5 6 7 8 9 10; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://drapps.drscca.org/) && [ "$c" = 200 ] && break; sleep 3
done
echo "drapps.drscca.org: $c"
[ "$c" = 200 ]
