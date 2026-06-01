# update.ps1 -- one-command updater for flow-bof-automation alpha users.
#
# Pulls the latest source from GitHub, preserves user data and local
# edits, and rebuilds + restarts the Docker services. Non-destructive
# to API keys, batches, and settings.
#
# Design notes:
#   * Treats the app source as replaceable. The user's edits are NOT
#     merged -- they're saved as a patch file in backups/update_<ts>/
#     and then the source is reset to origin/main.
#   * Backups happen BEFORE any destructive step. If a later step fails,
#     the backup is still there.
#   * Does not require admin privileges. Does not modify git config.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Flow BOF Automation Update" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Resolve project root from the script's location so the user can run
# this from anywhere (e.g. via Start menu) without losing context.
$Root = $PSScriptRoot
Set-Location $Root

# --- 1. Confirm this is a git repo --------------------------------------
& git rev-parse --is-inside-work-tree 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "This folder is not connected to GitHub." -ForegroundColor Red
    Write-Host "Please download the latest release manually or clone the repo." -ForegroundColor Yellow
    exit 1
}

# --- 2. Create the timestamped backup folder ----------------------------
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $Root "backups\update_$timestamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
Write-Host "Backup folder: backups\update_$timestamp" -ForegroundColor Gray
Write-Host ""

# Wrap the whole thing in a try so a failure anywhere prints a friendly
# message AND tells the user where their backup lives.
try {
    # --- 3. Back up user data ------------------------------------------
    Write-Host "Backing up your settings and batches..." -ForegroundColor Cyan

    $userPaths = @(
        ".env",
        "data\settings.local.json",
        "data\secrets.local.json",
        "data\batches",
        "data\unmatched_favorites.json",
        "data\video_submitted_tiles.json",
        "inputs\products.csv",
        "inputs\reference_images",
        "inputs\incoming_images",
        "outputs\logs"
    )
    foreach ($p in $userPaths) {
        $src = Join-Path $Root $p
        if (Test-Path $src) {
            $dst = Join-Path $backupDir $p
            $dstParent = Split-Path $dst -Parent
            New-Item -ItemType Directory -Path $dstParent -Force | Out-Null
            Copy-Item $src -Destination $dst -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    # --- 4. Save accidental local code changes as patches --------------
    Write-Host "Saving any local app edits just in case..." -ForegroundColor Cyan

    # `git diff` against HEAD prints any unstaged tracked-file edits.
    # We capture stdout to a file regardless of whether the file ends
    # up empty -- an empty patch is still informative ("clean working
    # tree"). Stderr is captured to a sidecar in case git complains.
    & git diff           2> (Join-Path $backupDir "git_diff.stderr.txt") |
        Out-File -FilePath (Join-Path $backupDir "local_changes.patch")  -Encoding utf8
    & git diff --staged  2> (Join-Path $backupDir "git_diff_staged.stderr.txt") |
        Out-File -FilePath (Join-Path $backupDir "staged_changes.patch") -Encoding utf8
    & git status --porcelain 2>$null |
        Out-File -FilePath (Join-Path $backupDir "git_status_before.txt") -Encoding utf8

    # --- 5. Stop containers --------------------------------------------
    Write-Host "Stopping app containers..." -ForegroundColor Cyan
    $dockerOk = $true
    try {
        $null = & docker info 2>&1
        if ($LASTEXITCODE -ne 0) { throw "docker not ready" }
        & docker compose down --remove-orphans 2>&1 | Out-Null
    } catch {
        Write-Host "  Docker isn't running -- skipping container stop." -ForegroundColor Yellow
        Write-Host "  We'll still update the code; you can rebuild later." -ForegroundColor Yellow
        $dockerOk = $false
    }

    # --- 6. Force-refresh the source from origin/main ------------------
    Write-Host "Downloading latest app version..." -ForegroundColor Cyan
    & git fetch origin
    if ($LASTEXITCODE -ne 0) {
        throw "git fetch origin failed (no internet? wrong remote?)."
    }
    & git reset --hard origin/main
    if ($LASTEXITCODE -ne 0) {
        throw "git reset --hard origin/main failed."
    }

    if (-not $dockerOk) {
        Write-Host ""
        Write-Host "Source updated, but Docker wasn't running so containers" -ForegroundColor Yellow
        Write-Host "weren't rebuilt or restarted. Start Docker Desktop and" -ForegroundColor Yellow
        Write-Host "then run:" -ForegroundColor Yellow
        Write-Host "    docker compose build" -ForegroundColor Yellow
        Write-Host "    docker compose up -d --force-recreate cdp-proxy ui" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Backup saved at backups\update_$timestamp" -ForegroundColor Gray
        exit 0
    }

    # --- 7. Rebuild ----------------------------------------------------
    Write-Host "Rebuilding app containers..." -ForegroundColor Cyan
    & docker compose build
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose build failed."
    }

    # --- 8. Restart ----------------------------------------------------
    Write-Host "Starting app..." -ForegroundColor Cyan
    & docker compose up -d --force-recreate cdp-proxy ui
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed."
    }

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host " Update complete. Open http://localhost:8080" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Backup saved at backups\update_$timestamp" -ForegroundColor Gray
    Write-Host "(If you intentionally edited source files, your changes" -ForegroundColor Gray
    Write-Host " were reset to the GitHub version but saved as a patch in" -ForegroundColor Gray
    Write-Host " that backup folder.)" -ForegroundColor Gray

} catch {
    Write-Host ""
    Write-Host "Update failed, but your backup is saved here:" -ForegroundColor Red
    Write-Host "    backups\update_$timestamp" -ForegroundColor Red
    Write-Host ""
    Write-Host "Technical error:" -ForegroundColor Yellow
    Write-Host "    $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "See docs\UPDATE.md for the manual recovery steps." -ForegroundColor Gray
    exit 1
}
