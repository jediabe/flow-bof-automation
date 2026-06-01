#!/usr/bin/env bash
# Launch host Google Chrome on macOS with remote debugging enabled.
# The Docker container reaches this Chrome via cdp-proxy:9333, which
# forwards to host.docker.internal:9222 (i.e. this Chrome).
#
# Three flags matter, all required:
#   --remote-debugging-port=9222
#       Opens the DevTools HTTP/WS endpoint.
#   --remote-debugging-address=0.0.0.0
#       Bind on all interfaces so the host-gateway -> 127.0.0.1 forward
#       used by Docker Desktop actually reaches Chrome. Without this the
#       endpoint only listens on the loopback interface and the docker
#       NAT can't deliver the packet.
#   --remote-allow-origins=*
#       Chrome 116+ enforces an Origin check on the WebSocket handshake
#       and rejects with 403 unless the requesting Origin is allow-listed.
#       Setting * is the simplest workaround -- only safe because port
#       9222 isn't exposed beyond the host's docker network in our setup.
#
# CRITICAL: close every existing Chrome window before running this.
# macOS will hand a fresh launch off to an existing Chrome process,
# which silently IGNORES the new command-line flags. The debug port
# either won't open or will still reject WebSockets.

set -euo pipefail

CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_DATA_DIR="${HOME}/chrome-flow-automation"
PORT=9222

if [[ ! -x "${CHROME_APP}" ]]; then
    echo "[FAIL] Google Chrome not found at /Applications/Google Chrome.app" >&2
    echo "       Install it from https://www.google.com/chrome/ and re-run." >&2
    exit 1
fi

# Warn loudly if Chrome is already running. We can't tell whether the
# running processes use OUR --user-data-dir, so we flag the risk and
# move on. The user can re-launch after closing Chrome if needed.
if pgrep -x "Google Chrome" >/dev/null 2>&1; then
    cat >&2 <<'WARN'

=========================== WARNING ===========================
 Chrome is already running.

 macOS will hand this launch off to the existing Chrome and
 SILENTLY IGNORE --remote-debugging-port / --remote-allow-origins.
 You'll see a new window open, but the CDP endpoint won't work
 (or will keep rejecting WebSocket handshakes with 403).

 If the Setup health check inside the UI reports "Chrome
 debugger unreachable", quit Chrome entirely (Cmd+Q in the
 menu bar) and re-run ./start.sh.
===============================================================

WARN
fi

mkdir -p "${USER_DATA_DIR}"

echo "Launching Chrome with:"
echo "  --remote-debugging-port=${PORT}"
echo "  --remote-debugging-address=0.0.0.0"
echo "  --remote-allow-origins=*"
echo "  --user-data-dir=${USER_DATA_DIR}"

# nohup + & detaches from this shell so start.sh can continue.
nohup "${CHROME_APP}" \
    --remote-debugging-port="${PORT}" \
    --remote-debugging-address=0.0.0.0 \
    --remote-allow-origins='*' \
    --user-data-dir="${USER_DATA_DIR}" \
    >/dev/null 2>&1 &

# Brief settle so 'pgrep' downstream sees the new process.
sleep 1

echo ""
echo "Chrome started. If you have never logged into Flow Labs in this"
echo "profile, go to https://labs.google/flow in that window and sign in."
