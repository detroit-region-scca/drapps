# shellcheck shell=bash
# Shared by on-server.sh (a deploy) and update.sh (the weekly update). Bash, run as root on the drapps VM.
# Source it: . /opt/drscca/app/deploy/gcp/lib.sh
#
# How a new version of the app goes live, the same way in both scripts:
#   1. build_candidate   builds the image "drapps:candidate". The running app is not touched.
#   2. test_candidate    runs the test suite inside that image, with no network and no server data.
#   3. keep_previous     tags the image that is running now "drapps:previous".
#   4. swap_in           tags the candidate "drapps:latest" and starts it.
#   5. healthy           asks the new container for its home page, from inside the container.
#   6. roll_back         if it never answers: starts "drapps:previous" again.

ROOT=${ROOT:-/opt/drscca}
APP=${APP:-drapps}            # the container and the compose service
APP_PORT=${APP_PORT:-5858}

say() { printf '%s\n' "$*"; }

# A message nobody can miss in a long log.
loud() {
	printf '\n%s\n' '######################################################################'
	printf '##  %s\n' "$@"
	printf '%s\n\n' '######################################################################'
}

# Run a command; show its output only if it fails. Keeps its exit code.
quiet() {
	local out rc=0
	out=$("$@" 2>&1) || rc=$?
	if [ "$rc" -ne 0 ]; then
		printf '%s\n' "$out"
	fi
	return "$rc"
}

image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }
running_image() { docker inspect -f '{{.Image}}' "$APP" 2>/dev/null || true; }
running_commit() { docker inspect -f '{{index .Config.Labels "org.drscca.commit"}}' "$APP" 2>/dev/null || true; }
is_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" = true ]; }

# build_candidate <commit> [extra docker build options]
build_candidate() {
	local sha=$1
	shift
	quiet docker build "$@" --build-arg "GIT_SHA=$sha" -t drapps:candidate "$ROOT/app"
}

# The test suite inside the candidate image: no network (so it cannot reach Google, QuickBooks or send mail), no
# volumes and no env file (so it cannot see the server's data or secrets).
test_candidate() {
	docker run --rm --network none -e OFFICIALS_HOST=/nonexistent -e PYTHONPATH=/app \
		drapps:candidate sh -c 'mkdir -p data && exec python -m pytest -q -p no:cacheprovider tests'
}

# Tag what is running now as the one to go back to. Skipped when the candidate is the same image (a deploy with no
# change to the app), so the real previous version is not forgotten.
keep_previous() {
	local now
	now=$(running_image)
	if [ -n "$now" ] && [ "$now" != "$(image_id drapps:candidate)" ]; then
		docker tag "$now" drapps:previous
	fi
}

swap_in() {
	docker tag drapps:candidate drapps:latest
	(cd "$ROOT" && quiet docker compose up -d --no-build "$APP")
}

# True once the app answers its home page with 200 twice in a row, asked from inside the container (so it is this
# container answering, not an older one behind Caddy). Gives up after about a minute and a half.
healthy() {
	local i ok=0
	for i in $(seq 1 30); do
		if is_running "$APP" && docker exec "$APP" python -c "
import sys, urllib.request
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$APP_PORT/', timeout=5).status == 200 else 1)" >/dev/null 2>&1; then
			ok=$((ok + 1))
			if [ "$ok" -ge 2 ]; then
				return 0
			fi
		else
			ok=0
			# Docker has restarted it three times already: it is not going to come up.
			if [ "$(docker inspect -f '{{.RestartCount}}' "$APP" 2>/dev/null || echo 0)" -ge 3 ]; then
				return 1
			fi
		fi
		sleep 3
	done
	return 1
}

# Start the previous image again. Returns 0 only if the app then answers.
roll_back() {
	if [ -z "$(image_id drapps:previous)" ]; then
		say "No drapps:previous image on this server: nothing to go back to."
		return 1
	fi
	docker tag drapps:previous drapps:latest
	(cd "$ROOT" && quiet docker compose up -d --no-build --force-recreate "$APP") || return 1
	healthy
}

# install_units <folder of unit files in the repo> <systemd's folder> <folder for copies of removed units>
# Makes the server's drscca-* job units match the repo: installs new and changed ones, removes ones the repo no
# longer has, and turns on every timer and path unit. Safe to run again and again.
install_units() {
	local src=$1 dst=$2 attic=$3 f name
	local changed=()
	for f in "$src"/drscca-*; do
		name=${f##*/}
		case $name in
			*.service | *.timer | *.path) ;;
			*) continue ;;
		esac
		if ! cmp -s "$f" "$dst/$name"; then
			install -m 644 "$f" "$dst/$name"
			changed+=("$name")
		fi
	done
	# Units this repo used to install and no longer has: stop them, turn them off, remove them. Only plain files
	# directly in systemd's folder named drscca-*.service, drscca-*.timer or drscca-*.path are ever looked at:
	# never a folder, a link, or a unit with any other name. A copy of each removed file is kept.
	for f in "$dst"/drscca-*; do
		if [ ! -f "$f" ] || [ -L "$f" ]; then
			continue
		fi
		name=${f##*/}
		case $name in
			drscca-*.service | drscca-*.timer | drscca-*.path) ;;
			*) continue ;;
		esac
		if [ -e "$src/$name" ]; then
			continue
		fi
		say "Removing a job that is no longer in the repo: $name (copy kept in $attic)"
		mkdir -p "$attic"
		cp -p "$f" "$attic/$name.$(date +%Y-%m-%d)"
		case $name in
			*@.service) ;;
			*) systemctl disable --now "$name" >/dev/null 2>&1 || true ;;
		esac
		rm -f "$f"
		systemctl reset-failed "$name" >/dev/null 2>&1 || true
	done
	systemctl daemon-reload
	# Every timer and every path unit in the repo is on. None is listed by name, so a new one cannot be missed.
	for f in "$src"/drscca-*.timer "$src"/drscca-*.path; do
		quiet systemctl enable --now "${f##*/}"
	done
	# A changed timer or path unit only takes effect when it is restarted (a new time, a new watched file).
	for name in ${changed[@]+"${changed[@]}"}; do
		case $name in
			*.timer | *.path) systemctl restart "$name" ;;
		esac
	done
}

# Old images and old build cache. The images named latest, previous and candidate are kept.
clean_up() {
	docker image prune -f >/dev/null || true
	docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
}

# Got Your Back (admin@'s mailbox for the off-site copy), kept at its newest release. GitHub publishes a sha256 for
# every release file; the download must match it or nothing changes. The new copy is unpacked beside the running one,
# must report its version and pass its own check against admin@ (the delegation scopes), and only then replaces it.
# Returns 1 (the caller says so) when a newer release could not be installed; the installed copy keeps working.
GYB_DIR=$ROOT/gyb
GYB_CONFIG=$ROOT/data/secrets/gyb
install_gyb() {
	local info ver url sha have t
	install -d -m 700 "$GYB_CONFIG"
	ln -sfn "$ROOT/data/secrets/workspace-sa.json" "$GYB_CONFIG/oauth2service.json"
	touch "$GYB_CONFIG/noupdatecheck.txt" "$GYB_CONFIG/nobrowser.txt"
	info=$(curl -fsSL https://api.github.com/repos/GAM-team/got-your-back/releases/latest | python3 -c '
import json, sys
d = json.load(sys.stdin)
a = next(a for a in d["assets"] if a["name"].endswith("-linux-x86_64-glibc2.35.tar.xz"))
print(d["tag_name"].lstrip("v"), a["browser_download_url"], (a.get("digest") or "").removeprefix("sha256:"))') || return 1
	read -r ver url sha <<<"$info"
	have=$("$GYB_DIR/gyb" --version 2>/dev/null | head -1 | awk '{print $NF}')
	[ "$have" = "$ver" ] && return 0
	[ -n "$sha" ] || { say "Got Your Back $ver: GitHub gave no checksum, not installed."; return 1; }
	t=$(mktemp -d)
	if curl -fsSL -o "$t/gyb.tar.xz" "$url" && echo "$sha  $t/gyb.tar.xz" | sha256sum -c --quiet - \
		&& mkdir "$t/new" && tar -xJf "$t/gyb.tar.xz" -C "$t/new" --strip-components=1 \
		&& "$t/new/gyb" --version | grep -q "Got Your Back $ver" \
		&& "$t/new/gyb" --config-folder "$GYB_CONFIG" --email admin@drscca.org --action check-service-account | grep -q 'All scopes passed'; then
		rm -rf "$GYB_DIR.previous"
		[ -d "$GYB_DIR" ] && mv "$GYB_DIR" "$GYB_DIR.previous"
		mv "$t/new" "$GYB_DIR"
		rm -rf "$t"
		say "Got Your Back ${have:-none} -> $ver."
		return 0
	fi
	rm -rf "$t"
	say "Got Your Back $ver did not download, verify or pass its check; ${have:-nothing} stays installed."
	return 1
}
