#!/usr/bin/env bash
# start.sh -- launch Chrome (with remote debugging) + Docker services
# and open the UI in the browser. macOS.

set -euo pipefail

cd "$(dirname "$0")"

printf "\n"
printf "============================================================\n"
printf " Flow BOF Automation -- start (macOS)\n"
printf "============================================================\n\n"

# 1. Quick sanity: docker is up.
if ! docker info >/dev/null 2>&1; then
    echo "[FAIL] Docker Desktop is not running." >&2
    echo "       Open Docker Desktop, wait for it to finish starting," >&2
    echo "       then re-run ./start.sh" >&2
    exit 1
fi

# 2. Chrome debug profile. The chrome script handles the "Chrome already
#    running" warning. We continue regardless so a misconfigured Chrome
#    doesn't block the UI from coming up.
echo "Launching Chrome with remote debugging..."
if [[ -x "scripts/start_chrome_debug.sh" ]]; then
    ./scripts/start_chrome_debug.sh || {
        echo "[WARN] Chrome launcher exited non-zero." >&2
        echo "       The UI will still come up; the in-app health check" >&2
        echo "       will tell you whether Docker can reach Chrome." >&2
    }
else
    echo "[WARN] scripts/start_chrome_debug.sh is not executable." >&2
    echo "       Fix with: chmod +x scripts/start_chrome_debug.sh" >&2
fi

# 3. Docker services. Only bring up what the user needs (cdp-proxy + ui);
#    the app service is on-demand via 'docker compose run --rm app ...'.
echo "Starting Docker services (cdp-proxy + ui)..."
docker compose up -d cdp-proxy ui

# 4. Wait for the UI to respond on :8080.
echo "Waiting for UI..."
ready=0
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null --max-time 1 "http://localhost:8080" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 1
done

if [[ "$ready" -eq 0 ]]; then
    echo "[WARN] UI didn't respond at http://localhost:8080 within 30s."
    echo "       Check 'docker compose logs ui'. The browser will still"
    echo "       open; refresh once the container is up."
else
    echo "[OK] UI is up."
fi

# 5. Open the UI in the default browser.
open "http://localhost:8080" 2>/dev/null || true

echo ""
echo "============================================================"
echo " Next steps:"
echo "   1. Log into Flow in the Chrome debug window that opened."
echo "      (https://labs.google/flow)"
echo "   2. In the UI (http://localhost:8080), open 'Setup' and"
echo "      enter your AI API key. Click 'Test API key' to verify."
echo "   3. Switch to 'BOF Batch Builder' and follow the steps."
echo "============================================================"
