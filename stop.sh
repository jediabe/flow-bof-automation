#!/usr/bin/env bash
# stop.sh -- shut down the Docker services. Data is preserved. macOS.

set -euo pipefail

cd "$(dirname "$0")"

# Same PATH guard as setup.sh / start.sh.
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"

# Resolve docker CLI even if Docker Desktop hasn't fully run its
# first-launch PATH install. We DON'T wait for the daemon here -- if
# the daemon is already down, `compose down` is a no-op and we want
# that to be fast.
# shellcheck disable=SC1091
source "scripts/_mac_docker_ready.sh"
if ! _find_docker_cli; then
    echo "[WARN] Could not locate the docker CLI. If Docker Desktop is" >&2
    echo "       running, the containers are already down. If not, install" >&2
    echo "       Docker Desktop from https://www.docker.com/products/docker-desktop/" >&2
    exit 0
fi

printf "\nStopping Docker services...\n"
if ! docker compose down --remove-orphans; then
    echo "[WARN] docker compose down exited non-zero." >&2
fi

# Offer to close Chrome.
printf "\nClose all Chrome windows too? (y/N) "
read -r reply
case "$reply" in
    y|Y|yes|YES)
        if command -v osascript >/dev/null 2>&1; then
            osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
            echo "[OK] Asked Chrome to quit."
        else
            pkill -x "Google Chrome" >/dev/null 2>&1 || true
            echo "[OK] Chrome stopped."
        fi
        ;;
    *)
        echo "Reminder: if you no longer need it, quit the Chrome debug"
        echo "window manually (the one launched by start.sh) so it doesn't"
        echo "hold the remote-debug port open in the background."
        ;;
esac

printf "\n[OK] Stopped. Your batches, settings, and API keys are preserved.\n"
echo "Run ./start.sh to resume."
