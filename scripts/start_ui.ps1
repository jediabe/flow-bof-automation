# Start the Streamlit UI in a detached container and open it in a browser.
#
# Order of operations:
#   1. Warn if the host Chrome debug profile isn't running.
#   2. docker compose up -d ui (also starts cdp-proxy via depends_on).
#   3. Wait briefly for Streamlit's HTTP listener to come up.
#   4. Open http://localhost:8080 in the default browser.
#
# Existing `docker compose run --rm app ...` CLI commands keep working
# in parallel. The UI does NOT replace them.
#
# NOTE: this file must stay ASCII-only. Windows PowerShell 5.1 defaults
# to the host's ANSI code page when reading scripts, so any UTF-8
# multibyte character (em-dash, ellipsis, curly quote) becomes garbage
# bytes that can include a literal " and break string parsing.

$ErrorActionPreference = "Stop"

# 1. Docker reachable?
try {
    docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker info failed" }
}
catch {
    Write-Host "Docker is not running. Start Docker Desktop and re-run." -ForegroundColor Red
    exit 1
}

# 2. Sanity-check host Chrome. The UI will still load if Chrome is down,
#    but --check-browser and the pipeline steps will fail until you start it.
$cdpOk = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:9222/json/version" `
                              -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($resp.StatusCode -eq 200) { $cdpOk = $true }
}
catch {}

if (-not $cdpOk) {
    Write-Host ""
    Write-Host "WARNING: host Chrome is NOT listening on port 9222." -ForegroundColor Yellow
    Write-Host "  Start it first: scripts\start_chrome_debug.ps1"   -ForegroundColor Yellow
    Write-Host "  Continuing -- the UI itself will still load."     -ForegroundColor Yellow
    Write-Host ""
}

# 3. Bring up the UI service (and cdp-proxy via depends_on).
Write-Host "docker compose up -d ui" -ForegroundColor Cyan
docker compose up -d ui
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose up failed." -ForegroundColor Red
    exit 1
}

# 4. Wait up to ~20 s for Streamlit to bind to 8080.
$uiUrl = "http://localhost:8080"
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $r = Invoke-WebRequest -Uri $uiUrl -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
            $ready = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $ready) {
    Write-Host "UI did not respond on $uiUrl after 20s." -ForegroundColor Yellow
    Write-Host "Tail logs: docker compose logs -f ui"    -ForegroundColor Yellow
    exit 1
}

Write-Host "UI ready at $uiUrl" -ForegroundColor Green
Start-Process $uiUrl

Write-Host ""
Write-Host "Tail logs:"                  -ForegroundColor Cyan
Write-Host "  docker compose logs -f ui" -ForegroundColor White
Write-Host "Stop UI:"                    -ForegroundColor Cyan
Write-Host "  docker compose stop ui"    -ForegroundColor White
Write-Host "Stop everything:"            -ForegroundColor Cyan
Write-Host "  scripts\stop_app.ps1"      -ForegroundColor White
