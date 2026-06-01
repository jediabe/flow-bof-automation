#!/usr/bin/env bash
# setup.sh -- one-time installer for flow-bof-automation alpha (macOS).
# Runs from the unzipped folder root. Idempotent: safe to re-run.

set -euo pipefail

cd "$(dirname "$0")"

printf "\n"
printf "============================================================\n"
printf " Flow BOF Automation -- setup (macOS)\n"
printf "============================================================\n\n"

# 1. Make sure Docker is installed AND ready. The helper handles the
#    full lifecycle: locates the docker CLI (PATH, Docker.app bundle,
#    or /usr/local/bin), opens Docker Desktop if it's installed but
#    dormant, and waits up to 180s for the daemon to respond. Sets
#    $DOCKER_BIN on success; exits on hard failure with a clear message.
# shellcheck disable=SC1091
source "scripts/_mac_docker_ready.sh"
ensure_docker_ready

# 2. Create the folders the app expects.
folders=(
    "data"
    "data/batches"
    "inputs"
    "inputs/reference_images"
    "inputs/incoming_images"
    "outputs"
    "outputs/images"
    "outputs/logs"
)
for f in "${folders[@]}"; do
    if [[ ! -d "$f" ]]; then
        mkdir -p "$f"
        echo "[+]  Created $f"
    fi
done
echo "[OK] Project folders ready."

# 3. Copy .env.example -> .env if missing (only as a template;
#    real keys go through the UI Setup page, not this file).
if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        cp ".env.example" ".env"
        echo "[+]  Created .env from .env.example (template values)."
    elif [[ -f ".env.docker.example" ]]; then
        cp ".env.docker.example" ".env"
        echo "[+]  Created .env from .env.docker.example."
    else
        echo "[!]  No .env.example found; skipping .env bootstrap."
    fi
else
    echo "[OK] .env already exists; leaving it alone."
fi

# 4. Make every shell helper executable. This is what trips first-time
#    Mac users -- the ZIP doesn't preserve the +x bit on some systems.
chmod +x setup.sh start.sh stop.sh 2>/dev/null || true
chmod +x scripts/start_chrome_debug.sh 2>/dev/null || true

# 5. Build Docker images.
echo ""
echo "Building Docker images (this can take a few minutes the first time)..."
"${DOCKER_BIN}" compose build

echo ""
echo "============================================================"
echo " Setup complete. Next:"
echo "   ./start.sh"
echo "============================================================"
