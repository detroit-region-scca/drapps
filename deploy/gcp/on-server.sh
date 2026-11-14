#!/bin/bash
# The server's half of a deploy. deploy.sh (on the maintainer's computer) brings the code in /opt/drscca/app to the
# new commit, then runs this as root:
#   bash /opt/drscca/app/deploy/gcp/on-server.sh <commit to deploy> [<commit the server was on before>]
#
# In order:
#   1. Build the new image and run the tests inside it. The running app is not touched. If either fails, the code
#      goes back to the commit it was on and the deploy stops.
#   2. Start the new image and ask it for its home page. If it does not answer, the previous image is started
#      again, the compose file and the code go back, and the deploy stops with a banner.
#   3. Only then the rest of the server setup: Caddy, live timing pages, job units, the host venv. A failure here
#      stops the deploy with the step named; the app stays on the new version.
# Every step can be run again safely.
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=deploy/gcp/lib.sh
. "$HERE/lib.sh"

want=${1:?usage: on-server.sh <commit> [<previous commit>]}
before=${2:-}
SRC=$ROOT/app/deploy/gcp
UNITS=${UNITS:-/etc/systemd/system}
STEP=start
LIVE=0      # 1 once the new version is running

on_error() {
	local rc=$?
	if [ "$LIVE" = 1 ]; then
		loud "DEPLOY NOT FINISHED: the step \"$STEP\" failed (exit $rc)." \
			"The app IS running the new version ($want)." \
			"The rest of the server setup did not finish. Fix the cause and deploy again."
	else
		loud "DEPLOY FAILED at \"$STEP\" (exit $rc)."
	fi
	exit 1
}
trap on_error ERR

step() {
	STEP=$1
	say "-- $1"
}

# Put the code back on the commit it was on, so the host jobs do not run code the app is not running.
# --keep refuses, and changes nothing, if a file was edited by hand on the server.
undo_code() {
	if [ -n "$before" ] && [ "$before" != "$(git -C "$ROOT/app" rev-parse HEAD)" ]; then
		if git -C "$ROOT/app" reset -q --keep "$before"; then
			say "Code on the server is back at ${before:0:7}."
		else
			say "Could not put the code back at ${before:0:7}: check /opt/drscca/app by hand (git status)."
		fi
	fi
}

cd "$ROOT"

step "check the code is at ${want:0:7}"
have=$(git -C app rev-parse HEAD)
if [ "$have" != "$want" ]; then
	loud "DEPLOY FAILED: the server's code is at ${have:0:7}, not ${want:0:7}." "Nothing was changed."
	exit 1
fi

# ---- 1. build and test: the running app is not touched ----

step "build the new image"
if ! build_candidate "$want"; then
	undo_code
	loud "DEPLOY FAILED: the image did not build." "The app is still running the version it was running."
	exit 1
fi

step "run the tests inside the new image"
if ! test_candidate; then
	undo_code
	loud "DEPLOY FAILED: the tests failed inside the new image." "The app is still running the version it was running."
	exit 1
fi

# ---- 2. start it, and go back if it does not answer ----

step "start the new version"
keep_previous
had_compose=0
if [ -f docker-compose.yml ]; then
	cp -p docker-compose.yml docker-compose.yml.previous
	had_compose=1
fi
cp "$SRC/docker-compose.yml" docker-compose.yml

started=1
swap_in || started=0
if [ "$started" = 1 ] && ! healthy; then
	started=0
fi
if [ "$started" = 1 ] && { [ "$(running_image)" != "$(image_id drapps:candidate)" ] || [ "$(running_commit)" != "$want" ]; }; then
	say "The running container is not the new image (it reports commit \"$(running_commit)\")."
	started=0
fi

if [ "$started" != 1 ]; then
	say "The new version did not start. Its last log lines:"
	docker logs --tail 40 "$APP" 2>&1 || true
	# Put the compose file from before back, unless it is from before "image: drapps:latest" (the first deploy
	# with this script): that one names another image, and the new file starts the previous image just as well.
	if [ "$had_compose" = 1 ] && grep -q 'image: drapps:latest' docker-compose.yml.previous; then
		cp docker-compose.yml.previous docker-compose.yml
	fi
	if roll_back; then
		undo_code
		loud "DEPLOY FAILED AND WAS ROLLED BACK." \
			"The new version ($want) did not start." \
			"The app is running the PREVIOUS version again (commit \"$(running_commit)\")." \
			"Read the log lines above, fix the code, and deploy again."
	else
		undo_code
		loud "DEPLOY FAILED AND THE ROLLBACK FAILED TOO. THE SITE MAY BE DOWN." \
			"On the server: cd /opt/drscca && sudo docker compose ps && sudo docker logs --tail 80 drapps" \
			"See docs/RUNBOOK.md, \"A deploy failed\"."
	fi
	exit 1
fi
LIVE=1
say "drapps is running the new image (commit ${want:0:7})."

# ---- 3. the rest of the server setup ----

step "Caddy configuration"
# The Caddyfile and the nginx config are single files mounted into containers: copy over them in place (cp), never
# replace them (install, mv), or the container keeps reading the old file.
if ! cmp -s "$SRC/Caddyfile" Caddyfile; then
	if is_running caddy; then
		# Have the running Caddy read the new file first: a typo must not take both sites down.
		docker exec -i caddy sh -c 'cat > /tmp/Caddyfile.new && caddy adapt --config /tmp/Caddyfile.new --adapter caddyfile >/dev/null' < "$SRC/Caddyfile"
	fi
	if [ -f Caddyfile ]; then
		cp -p Caddyfile Caddyfile.previous
	fi
	cp "$SRC/Caddyfile" Caddyfile
fi
if ! cmp -s "$SRC/livetiming-nginx.conf" livetiming-nginx.conf; then
	cp "$SRC/livetiming-nginx.conf" livetiming-nginx.conf
	nginx_changed=1
else
	nginx_changed=0
fi

step "QuickBooks token folder and helper"
install -m 755 "$SRC/qbo-mcp.sh" "$ROOT/qbo-mcp.sh"
# QuickBooks swaps its token on use and saves the new one with a temp file + rename, so the token needs a folder
# drapps can write (the secrets folder belongs to root): a token it cannot save goes stale and QuickBooks refuses it.
install -d -o drapps -g drapps -m 700 data/secrets/qbo
if [ -f data/secrets/qbo.env ] && [ ! -f data/secrets/qbo/qbo.env ]; then
	mv data/secrets/qbo.env data/secrets/qbo/qbo.env
fi

step "live timing pages"
mkdir -p data/livetiming/www
install -m 644 "$SRC"/livetiming-www/*.html data/livetiming/www/

step "job units"
install_units "$SRC/systemd" "$UNITS" "$ROOT/data/removed-units"

step "fail2ban, backup tools, apt settings, time zone"
if ! command -v fail2ban-client >/dev/null; then
	quiet apt-get install -y -qq fail2ban
fi
# The nightly off-site copy (drscca-offsite-backup): restic (0.17.1 or newer, Debian 13), and rclone with FUSE to
# read the shared drives. The QuickBooks sync leaves the books copy in /var/lib/drscca-quickbooks.
if ! command -v restic >/dev/null || ! command -v rclone >/dev/null || ! command -v fusermount3 >/dev/null; then
	quiet apt-get install -y -qq restic rclone fuse3
fi
# Got Your Back (admin@'s mailbox for the off-site copy): its newest release, checksum-verified (lib.sh). A failure
# here is reported but does not stop the deploy: the copy already installed keeps working.
install_gyb || say "Got Your Back was not updated (see above)."
install -d -o drapps -g drapps -m 750 /var/lib/drscca-quickbooks
install -m 644 "$SRC/fail2ban/livetiming-sftp.local" /etc/fail2ban/jail.d/livetiming-sftp.local
systemctl reload-or-restart fail2ban
install -m 644 "$SRC/apt/52drscca-upgrades" /etc/apt/apt.conf.d/52drscca-upgrades
if [ "$(timedatectl show -p Timezone --value)" != America/Detroit ]; then
	timedatectl set-timezone America/Detroit
fi

step "Python libraries for the host jobs"
# The host jobs (QuickBooks sync, officials, backups, the alert email) run from /opt/drscca/venv, not the container.
if [ ! -x venv/bin/pip ]; then
	python3 -m venv venv
fi
# gam7: the Email groups pull and Hassles subscribe use GAM from this venv (the weekly update keeps it current).
quiet venv/bin/pip install --quiet --disable-pip-version-check gam7 -r app/requirements.txt

step "the other containers"
quiet docker compose up -d --no-build --remove-orphans
if [ "$nginx_changed" = 1 ] && is_running livetiming-web; then
	docker exec livetiming-web nginx -t >/dev/null 2>&1 && docker exec livetiming-web nginx -s reload >/dev/null
fi

step "reload Caddy"
if is_running caddy; then
	if ! quiet docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
		if [ -f Caddyfile.previous ]; then
			cp Caddyfile.previous Caddyfile
			docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
			say "Caddy refused the new Caddyfile; the previous one is back in place."
		fi
		false
	fi
fi

step "clean up"
clean_up
# The image name compose used before "image: drapps:latest" was set. Only the name goes; an image in use stays.
docker image rm drscca-drapps:latest >/dev/null 2>&1 || true

trap - ERR
say "drapps deploy ok: $want"
