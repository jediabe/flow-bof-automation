#!/usr/bin/env bash
# setup.sh -- one-time installer for flow-bof-automation alpha (macOS).
# Runs from the unzipped folder root. Idempotent: safe to re-run.

set -euo pipefail

cd "$(dirname "$0")"

# Put Docker Desktop's bin dir at the front of PATH BEFORE we source the
# helper or call docker. This ensures both `docker` and its sidecar
# `docker-credential-desktop` resolve -- without it, `docker compose
# build` fails with "docker-credential-desktop: executable file not
# found in $PATH". The helper also exports this PATH on source for
# belt-and-suspenders; we set it here too so even error paths before
# the source call (none today, but future-proof) still benefit.
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"

printf "\n"
printf "============================================================\n"
printf " Flow BOF Automation -- setup (macOS)\n"
printf "============================================================\n\n"

# 1. Make sure Docker is installed AND ready. The helper locates the
#    docker CLI, opens Docker Desktop if it's installed but dormant,
#    and waits up to 180s for the daemon. Exits on hard failure with
#    a clear message.
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

# 3. Copy .env.example -> .env if missing.
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

# 4. Make every shell helper executable. ZIP extraction loses +x on
#    some Mac extractors.
chmod +x setup.sh start.sh stop.sh 2>/dev/null || true
chmod +x scripts/start_chrome_debug.sh 2>/dev/null || true

# 5. Build Docker images. If the build fails, the user almost always
#    needs to see the tail of the build log to know why -- print a
#    pointer instead of leaving them with a stack trace.
echo ""
echo "Building Docker images (this can take a few minutes the first time)..."
if ! docker compose build; then
    cat >&2 <<'ERR'

[FAIL] docker compose build failed. Common causes:
  - Docker Desktop's credential helper isn't on PATH. Reopen Docker
    Desktop, then rerun ./setup.sh.
  - A base image is being throttled. Wait 60s and rerun.
  - Apple Silicon manifest mismatch. Rerun with:
        docker compose build --no-cache

After fixing, you can inspect the most recent build attempt with:
        docker compose logs --tail=200

ERR
    exit 1
fi
echo "[OK] Images built."

echo ""
echo "============================================================"
echo " Setup complete. Next:"
echo "   ./start.sh"
echo "============================================================"
