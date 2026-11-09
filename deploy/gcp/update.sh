#!/bin/bash
# Weekly (drscca-update.timer, Sundays 4:30 am): newest Caddy, nginx and SFTP images; drapps rebuilt on the newest
# Python base image; newest GAM and Python libraries for the host jobs; then old images cleared. Debian, Docker and
# Node packages are updated daily by unattended-upgrades instead.
#
# The rebuilt drapps image is tested before it replaces the running one (deploy/gcp/lib.sh): if the tests fail the
# old one keeps running, and if the new one does not start the old one is started again. Either way this script
# ends with a non-zero exit, so the "weekly software update failed" email goes out.
# The libraries inside the image only change when the base image changed or requirements.txt did: Docker reuses the
# pip step otherwise.
set -Eeuo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=deploy/gcp/lib.sh
. "$HERE/lib.sh"
cd "$ROOT"

docker compose pull --quiet caddy livetiming-sftp livetiming-web

problem=''
sha=$(git -C app rev-parse HEAD)
if ! build_candidate "$sha" --pull; then
	problem='the new drapps image did not build; the app keeps running the old one'
elif ! test_candidate; then
	problem='the tests failed inside the new drapps image; the app keeps running the old one'
else
	keep_previous
	if swap_in && healthy; then
		say "drapps: running the image built today (commit ${sha:0:7})."
	else
		say "The rebuilt drapps did not start. Its last log lines:"
		docker logs --tail 40 "$APP" 2>&1 || true
		if roll_back; then
			problem='the rebuilt drapps did not start; ROLLED BACK to the previous image, which is running'
		else
			problem='the rebuilt drapps did not start AND the rollback failed: THE SITE MAY BE DOWN'
		fi
	fi
fi

# The other containers, whatever happened to drapps (--no-build: drapps only ever starts a tested image).
docker compose up -d --no-build --remove-orphans
clean_up

if [ -z "$problem" ]; then
	# Host jobs. Skipped after a failure above: the same new library versions are the likely cause.
	"$ROOT/venv/bin/pip" install --quiet --upgrade pip gam7 -r "$ROOT/app/requirements.txt"
fi
docker compose ps --format '{{.Name}} {{.Status}}'

if [ -n "$problem" ]; then
	loud "WEEKLY UPDATE FAILED: $problem."
	exit 1
fi
