#!/usr/bin/env bash
# start.sh -- launch Chrome (with remote debugging) + Docker services
# and open the UI in the browser. macOS.

set -euo pipefail

cd "$(dirname "$0")"

# Same PATH guard as setup.sh -- see comment there. Without this the
# Docker credential helper can't be found, and any compose commands
# that trigger image pulls or logins fail mysteriously.
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"

printf "\n"
printf "============================================================\n"
printf " Flow BOF Automation -- start (macOS)\n"
printf "============================================================\n\n"

# 1. Make sure Docker is ready.
# shellcheck disable=SC1091
source "scripts/_mac_docker_ready.sh"
ensure_docker_ready

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

# 3. Docker services. Only bring up what the user needs (cdp-proxy + ui).
echo "Starting Docker services (cdp-proxy + ui)..."
if ! docker compose up -d cdp-proxy ui; then
    cat >&2 <<'ERR'

[FAIL] docker compose up failed. Inspect the most recent attempt:
        docker compose ps
        docker compose logs ui --tail=100
ERR
    exit 1
fi

# 4. Wait for the UI to actually respond on :8080 -- up to 60s. We do
#    NOT report success based on the compose command alone; the
#    container can be "up" while Streamlit is still importing modules,
#    and the user sees "localhost refused" if they open the browser
#    too soon.
echo "Waiting for UI on http://localhost:8080 (up to 60s)..."
ready=0
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null --max-time 1 "http://localhost:8080" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 1
done

if [[ "$ready" -eq 0 ]]; then
    cat >&2 <<'ERR'

[FAIL] UI did not respond at http://localhost:8080 within 60s.
       Diagnostic dump follows -- share this with support if needed.
ERR
    echo "" >&2
    echo "--- docker compose ps ---" >&2
    docker compose ps >&2 || true
    echo "" >&2
    echo "--- docker compose logs ui --tail=100 ---" >&2
    docker compose logs ui --tail=100 >&2 || true
    echo "" >&2
    cat >&2 <<'ERR'

Common causes:
  - Streamlit crashed on import. Check the log block above for a
    Python traceback.
  - Port 8080 is occupied by another process. Stop that process and
    rerun ./start.sh.
  - The image was never built. Run ./setup.sh first.

Once you've fixed the issue, rerun ./start.sh.
ERR
    exit 1
fi

echo "[OK] UI is up at http://localhost:8080."

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
