#!/usr/bin/env bash
# stop.sh -- shut down the Docker services. Data is preserved. macOS.

set -euo pipefail

cd "$(dirname "$0")"

printf "\nStopping Docker services...\n"
if ! docker compose down --remove-orphans; then
    echo "[WARN] docker compose down exited non-zero." >&2
fi

# Offer to close Chrome. On macOS we ask first because users often
# keep other Chrome windows open they don't want killed.
printf "\nClose all Chrome windows too? (y/N) "
read -r reply
case "$reply" in
    y|Y|yes|YES)
        # AppleScript is the polite way: it asks Chrome to quit, lets it
        # save state, and won't kill unrelated processes.
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
