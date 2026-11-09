#!/bin/sh
# Deploy drapps to the Google Cloud VM (run on bodhi, from the repo, after committing):
#   deploy/gcp/deploy.sh
#
# 1. Refuses uncommitted changes, runs the tests here, pushes. A failing test stops everything.
# 2. On the VM: brings /opt/drscca/app to exactly this commit, then runs deploy/gcp/on-server.sh, which builds the
#    new image, runs the tests inside it, starts it, checks it answers, and goes back to the previous image by
#    itself if it does not. Then it installs the Caddyfile, job units and host libraries.
# 3. Confirms the server reported this commit as running, and that the public site answers.
# Any failure stops the deploy with a non-zero exit and the error on screen. See docs/RUNBOOK.md, "Changing the app".
#
# POSIX sh on purpose: the drapps-deploy wrapper runs this with "sh".
set -eu
cd "$(dirname "$0")/../.."
VM="gcloud compute ssh drapps --project drscca-automation --zone us-central1-a --tunnel-through-iap --quiet --command"
SITE=https://drapps.drscca.org

fail() {
	echo ""
	echo "######################################################################"
	echo "##  DEPLOY FAILED: $1"
	echo "######################################################################"
	exit 1
}

echo "== tests"
if [ -n "$(git status --porcelain)" ]; then
	fail "uncommitted changes. Commit first. Nothing was pushed or deployed."
fi
# No pipe here: a pipe would hide pytest's exit code.
if ! OFFICIALS_HOST=/nonexistent PYTHONPATH=. venv/bin/python -m pytest -q tests; then
	fail "the tests failed. Nothing was pushed or deployed."
fi

echo "== push"
SHA=$(git rev-parse HEAD)
git push -q origin HEAD || fail "git push failed. Nothing was deployed."

echo "== deploy on the VM ($(git log -1 --format='%h %s'))"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo 1 > "$TMP/rc"
# The server moves to exactly the commit tested above (not "whatever the branch is now"), then runs its half.
REMOTE="set -e; cd /opt/drscca/app; before=\$(sudo git rev-parse HEAD); sudo git fetch -q origin; sudo git merge -q --ff-only $SHA; exec sudo bash deploy/gcp/on-server.sh $SHA \$before"
# The filter drops gcloud's chatter. The remote exit code is saved to a file first, because a pipe reports only the
# last command's exit code (the filter's).
{
	rc=0
	$VM "$REMOTE" 2>&1 || rc=$?
	echo "$rc" > "$TMP/rc"
} | tee "$TMP/log" | grep --line-buffered -v -i -e numpy -e "tunnel" -e "^WARNING" -e "please see" -e "^$" || true
rc=$(cat "$TMP/rc")
if [ "$rc" != 0 ]; then
	fail "the server's half of the deploy ended with exit code $rc. Read the lines above."
fi
if ! grep -q "drapps deploy ok: $SHA" "$TMP/log"; then
	fail "the server did not report commit $SHA as deployed. Read the lines above."
fi

echo "== check"
c=000
for i in 1 2 3 4 5 6 7 8 9 10; do
	c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SITE/") || true
	if [ "$c" = 200 ]; then
		break
	fi
	sleep 3
done
echo "$SITE: $c"
if [ "$c" != 200 ]; then
	fail "the server runs the new version, but $SITE answered $c from here. Check Caddy: sudo docker logs --tail 50 caddy"
fi
if ! curl -s -o /dev/null -D - --max-time 10 "$SITE/" | grep -qi '^strict-transport-security:'; then
	echo "Warning: $SITE did not send the security headers from deploy/gcp/Caddyfile. Check that Caddy reloaded."
fi
echo "Deployed $SHA."
