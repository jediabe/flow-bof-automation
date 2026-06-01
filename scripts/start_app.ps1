# Sanity-checks the Docker setup and prints the daily-flow commands.
#
# Does NOT auto-run any pipeline command -- those have side effects (CSV
# rewrites, browser interactions). Use the printed commands as your
# reference and run them explicitly when you mean to.

$ErrorActionPreference = "Stop"

Write-Host "==== flow-bof-automation (Docker) ====" -ForegroundColor Cyan
Write-Host ""

# 1. Docker Desktop reachable?
try {
    docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker info failed" }
}
catch {
    Write-Host "Docker is not running. Start Docker Desktop and re-run." -ForegroundColor Red
    exit 1
}

# 2. Image built?
$image = docker images -q flow-bof-automation:latest 2>$null
if (-not $image) {
    Write-Host "Image not built yet. Building now (one-time)..." -ForegroundColor Yellow
    docker compose build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker compose build failed. Fix and re-run." -ForegroundColor Red
        exit 1
    }
}

# 3. Is Chrome's debug port listening on the host?
$cdpOk = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:9222/json/version" `
                              -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        $cdpOk = $true
        $ver  = ($resp.Content | ConvertFrom-Json).Browser
        Write-Host "Chrome CDP detected: $ver" -ForegroundColor Green
    }
}
catch {
    # Fall through to the warning below.
}

if (-not $cdpOk) {
    Write-Host "Chrome remote debugging is NOT listening on port 9222." -ForegroundColor Red
    Write-Host "Start it first (from a second PowerShell):"             -ForegroundColor Yellow
    Write-Host "  scripts\start_chrome_debug.ps1"                        -ForegroundColor Yellow
    Write-Host ""
}

# 4. Print canonical commands.
Write-Host ""
Write-Host "Smoke-test the in-container CDP proxy (optional but useful):" -ForegroundColor Cyan
Write-Host '  docker compose run --rm app python -c "import urllib.request; print(urllib.request.urlopen(''http://cdp-proxy:9333/json/version'').read().decode()[:500])"' -ForegroundColor White
Write-Host ""
Write-Host "Common commands (copy/paste as needed):" -ForegroundColor Cyan
Write-Host ""
Write-Host "  docker compose run --rm app python main.py --check-browser"          -ForegroundColor White
Write-Host "  docker compose run --rm app python main.py --validate-manifest"      -ForegroundColor White
Write-Host "  docker compose run --rm app python main.py --load-manifest --fresh"  -ForegroundColor White
Write-Host "  docker compose run --rm app python main.py --list-status"            -ForegroundColor White
Write-Host "  docker compose run --rm app python main.py --generate-images --limit 30" -ForegroundColor White
Write-Host "  docker compose run --rm app python main.py --sync-favorites"         -ForegroundColor White
Write-Host "  docker compose run --rm app python main.py --generate-videos --limit 30"  -ForegroundColor White
Write-Host ""
Write-Host "Stop containers / clean up:"   -ForegroundColor Cyan
Write-Host "  scripts\stop_app.ps1"        -ForegroundColor White
Write-Host ""
