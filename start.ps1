# start.ps1 -- launch Chrome (with remote debugging) + Docker services
# and open the UI in the browser.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Flow BOF Automation -- start" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Quick sanity: docker is up.
try {
    $null = & docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker info exit $LASTEXITCODE" }
} catch {
    Write-Host "[FAIL] Docker Desktop is not running. Start it, then re-run start.ps1." -ForegroundColor Red
    exit 1
}

# 2. Chrome debug profile. The chrome script already handles the
#    "existing Chrome must be closed" warning, so we just hand off.
Write-Host "Launching Chrome with remote debugging..." -ForegroundColor Cyan
& "$PSScriptRoot\scripts\start_chrome_debug.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Chrome launch exited with code $LASTEXITCODE." -ForegroundColor Yellow
    Write-Host "       You can still use the UI; the Setup health check will tell you" -ForegroundColor Yellow
    Write-Host "       whether the Docker container can reach Chrome." -ForegroundColor Yellow
}

# 3. Docker services. Only bring up what the user needs (cdp-proxy + ui);
#    the app service is on-demand via 'docker compose run --rm app ...'.
Write-Host "Starting Docker services (cdp-proxy + ui)..." -ForegroundColor Cyan
& docker compose up -d cdp-proxy ui
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker compose up failed. Check 'docker compose logs ui'." -ForegroundColor Red
    exit 1
}

# 4. Wait for the UI to respond on :8080.
Write-Host "Waiting for UI..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8080" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        if ($resp.StatusCode -lt 500) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    Write-Host "[WARN] UI didn't respond at http://localhost:8080 within 30s." -ForegroundColor Yellow
    Write-Host "       Check 'docker compose logs ui'. The browser will still open;" -ForegroundColor Yellow
    Write-Host "       refresh once the container is up." -ForegroundColor Yellow
} else {
    Write-Host "[OK] UI is up." -ForegroundColor Green
}

# 5. Open the UI in the default browser.
Start-Process "http://localhost:8080"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Next steps:" -ForegroundColor Green
Write-Host "   1. Log into Flow in the Chrome window that just opened." -ForegroundColor Green
Write-Host "   2. In the UI (http://localhost:8080), open 'Setup' and" -ForegroundColor Green
Write-Host "      enter your AI API key. Click 'Test API key' to verify." -ForegroundColor Green
Write-Host "   3. Switch to 'BOF Batch Builder' and follow the steps." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
