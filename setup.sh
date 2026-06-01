#!/usr/bin/env bash
# setup.sh -- one-time installer for flow-bof-automation alpha (macOS).
# Runs from the unzipped folder root. Idempotent: safe to re-run.

set -euo pipefail

cd "$(dirname "$0")"

printf "\n"
printf "============================================================\n"
printf " Flow BOF Automation -- setup (macOS)\n"
printf "============================================================\n\n"

# 1. Verify Docker is installed AND its daemon is reachable.
if ! command -v docker >/dev/null 2>&1; then
    echo "[FAIL] Docker is not installed on this Mac." >&2
    echo "       Install Docker Desktop for Mac:" >&2
    echo "       https://www.docker.com/products/docker-desktop/" >&2
    echo "       Then re-run ./setup.sh" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "[FAIL] Docker is installed but the daemon isn't responding." >&2
    echo "       Open Docker Desktop and wait for the whale icon to" >&2
    echo "       stop animating, then re-run ./setup.sh" >&2
    exit 1
fi
echo "[OK] Docker Desktop is running."

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
docker compose build

echo ""
echo "============================================================"
echo " Setup complete. Next:"
echo "   ./start.sh"
echo "============================================================"
