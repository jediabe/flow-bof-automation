#!/usr/bin/env bash
# update.sh -- one-command updater for flow-bof-automation alpha users.
# macOS.
#
# See update.ps1 for the design rationale. The two scripts intentionally
# do the same thing in their host's native language.

set -euo pipefail

cd "$(dirname "$0")"

# Same PATH guard as setup.sh / start.sh so docker + docker-credential-desktop
# resolve even if Docker Desktop's first-launch PATH install hasn't happened
# yet on this terminal.
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"

printf "\n"
printf "============================================================\n"
printf " Flow BOF Automation Update\n"
printf "============================================================\n\n"

# --- 1. Confirm this is a git repo --------------------------------------
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "This folder is not connected to GitHub." >&2
    echo "Please download the latest release manually or clone the repo." >&2
    exit 1
fi

# --- 2. Create the timestamped backup folder ----------------------------
timestamp="$(date +"%Y%m%d_%H%M%S")"
backupDir="backups/update_${timestamp}"
mkdir -p "${backupDir}"
echo "Backup folder: ${backupDir}"
echo ""

# `_fail` is invoked from the EXIT trap on a non-zero exit. It tells
# the user where their backup is regardless of what blew up.
update_failed=0
trap '
    if [[ "${update_failed}" -ne 0 ]]; then
        echo "" >&2
        echo "Update failed, but your backup is saved here:" >&2
        echo "    ${backupDir}" >&2
        echo "" >&2
        echo "See docs/UPDATE.md for the manual recovery steps." >&2
    fi
' EXIT

# Helper: print failure context + flip the flag so the EXIT trap prints
# the friendly message, then exit non-zero.
_die() {
    update_failed=1
    echo "" >&2
    echo "Technical error: $*" >&2
    exit 1
}

# --- 3. Back up user data ----------------------------------------------
echo "Backing up your settings and batches..."

user_paths=(
    ".env"
    "data/settings.local.json"
    "data/secrets.local.json"
    "data/batches"
    "data/unmatched_favorites.json"
    "data/video_submitted_tiles.json"
    "inputs/products.csv"
    "inputs/reference_images"
    "inputs/incoming_images"
    "outputs/logs"
)
for p in "${user_paths[@]}"; do
    if [[ -e "$p" ]]; then
        # Recreate the path structure under the backup dir so it's
        # obvious where each item came from.
        parent="$(dirname "${backupDir}/${p}")"
        mkdir -p "${parent}"
        # -R preserves dirs, -p preserves timestamps where possible.
        # Trailing '2>/dev/null || true' so a permission glitch on one
        # file doesn't abort the whole backup loop.
        cp -Rp "$p" "${backupDir}/${p}" 2>/dev/null || true
    fi
done

# --- 4. Save accidental local code changes as patches ------------------
echo "Saving any local app edits just in case..."

git diff           > "${backupDir}/local_changes.patch"   2> "${backupDir}/git_diff.stderr.txt" || true
git diff --staged  > "${backupDir}/staged_changes.patch"  2> "${backupDir}/git_diff_staged.stderr.txt" || true
git status --porcelain > "${backupDir}/git_status_before.txt" 2>/dev/null || true

# --- 5. Stop containers ------------------------------------------------
echo "Stopping app containers..."
docker_ok=1
if ! docker info >/dev/null 2>&1; then
    echo "  Docker isn't running -- skipping container stop."
    echo "  We'll still update the code; you can rebuild later."
    docker_ok=0
else
    docker compose down --remove-orphans >/dev/null 2>&1 || true
fi

# --- 6. Force-refresh the source from origin/main ----------------------
echo "Downloading latest app version..."
if ! git fetch origin; then
    _die "git fetch origin failed (no internet? wrong remote?)."
fi
if ! git reset --hard origin/main; then
    _die "git reset --hard origin/main failed."
fi

if [[ "${docker_ok}" -eq 0 ]]; then
    echo ""
    echo "Source updated, but Docker wasn't running so containers"
    echo "weren't rebuilt or restarted. Start Docker Desktop and"
    echo "then run:"
    echo "    docker compose build"
    echo "    docker compose up -d --force-recreate cdp-proxy ui"
    echo ""
    echo "Backup saved at ${backupDir}"
    exit 0
fi

# --- 7. Rebuild --------------------------------------------------------
echo "Rebuilding app containers..."
if ! docker compose build; then
    _die "docker compose build failed."
fi

# --- 8. Restart --------------------------------------------------------
echo "Starting app..."
if ! docker compose up -d --force-recreate cdp-proxy ui; then
    _die "docker compose up failed."
fi

echo ""
echo "============================================================"
echo " Update complete. Open http://localhost:8080"
echo "============================================================"
echo ""
echo "Backup saved at ${backupDir}"
echo "(If you intentionally edited source files, your changes were"
echo " reset to the GitHub version but saved as a patch in that"
echo " backup folder.)"
