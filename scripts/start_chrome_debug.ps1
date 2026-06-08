# Launch real Chrome on the Windows host with remote-debugging enabled.
# The Docker container reaches this Chrome via cdp-proxy:9333, which
# forwards to host.docker.internal:9222 (i.e. this Chrome).
#
# Three CDP flags matter, all required:
#   --remote-debugging-port=9222
#       Opens the DevTools HTTP/WS endpoint.
#   --remote-debugging-address=0.0.0.0
#       Bind on all interfaces so the host-gateway -> 127.0.0.1 forward
#       used by Docker Desktop actually reaches Chrome. Without this the
#       endpoint only listens on the loopback interface and the docker
#       NAT can't deliver the packet.
#   --remote-allow-origins=*
#       Chrome 116+ enforces an Origin check on the WebSocket handshake
#       and rejects with 403 "Rejected an incoming WebSocket connection
#       from the http://127.0.0.1:9222 origin" unless the requesting
#       Origin is explicitly allow-listed. Setting * is the simplest
#       workaround -- only safe because port 9222 isn't exposed beyond
#       the host's docker network in our setup.
#
# Anti-fingerprint (navigator.webdriver suppression) used to live
# here as `--disable-blink-features=AutomationControlled`. We removed
# it because Chrome shows a yellow "you are using an unsupported
# command-line flag" infobar whenever that flag is set — itself a
# visible signal that the browser is automated, and likely something
# Google's risk engine can detect. The same fingerprint suppression
# now happens INSIDE the runner via a CDP init script (see
# flow_automation.py:_STEALTH_INIT_JS) so users get the benefit
# without the banner.
#
# CRITICAL: close every existing Chrome window before running this.
# If any Chrome process is alive, Windows will hand the new launch off
# to it, which silently IGNORES the new command-line flags. The debug
# port either won't open at all or will still reject WebSockets.

$ErrorActionPreference = "Stop"

$ChromePath  = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$UserDataDir = Join-Path $env:USERPROFILE "chrome-flow-automation"
$Port        = 9222

if (-not (Test-Path $ChromePath)) {
    Write-Host "Chrome not found at $ChromePath" -ForegroundColor Red
    Write-Host "Edit this script if Chrome lives elsewhere on your machine." -ForegroundColor Yellow
    exit 1
}

# Warn if Chrome is already running. PowerShell can't tell us if those
# processes happen to be from our own --user-data-dir, so we just flag
# the risk loudly.
$existing = Get-Process -Name chrome -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host ""
    Write-Host "=========================== WARNING ===========================" -ForegroundColor Red
    Write-Host " Chrome is already running ($($existing.Count) process(es))." -ForegroundColor Red
    Write-Host ""
    Write-Host " Windows will hand this launch off to the existing Chrome and" -ForegroundColor Red
    Write-Host " SILENTLY IGNORE --remote-debugging-port / --remote-allow-origins." -ForegroundColor Red
    Write-Host " You'll see the new window open, but the CDP endpoint won't" -ForegroundColor Red
    Write-Host " work or will keep rejecting WebSocket handshakes with 403." -ForegroundColor Red
    Write-Host ""
    Write-Host " Close ALL Chrome windows, then re-run this script." -ForegroundColor Yellow
    Write-Host "===============================================================" -ForegroundColor Red
    Write-Host ""
    $reply = Read-Host "Continue anyway? (y/N)"
    if ($reply -ne "y" -and $reply -ne "Y") {
        Write-Host "Aborted. Close Chrome and re-run." -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "Launching Chrome with:"                                    -ForegroundColor Cyan
Write-Host "  --remote-debugging-port=$Port"                           -ForegroundColor Cyan
Write-Host "  --remote-debugging-address=0.0.0.0"                      -ForegroundColor Cyan
Write-Host "  --remote-allow-origins=*"                                -ForegroundColor Cyan
Write-Host "  --user-data-dir=$UserDataDir"                            -ForegroundColor Cyan
Write-Host ""
Write-Host "First-time setup in the new Chrome window:" -ForegroundColor Yellow
Write-Host "  1. Sign in to your Google account."       -ForegroundColor Yellow
Write-Host "  2. Open https://labs.google/flow"          -ForegroundColor Yellow
Write-Host ""
Write-Host "Then, from another PowerShell:"             -ForegroundColor Yellow
Write-Host "  scripts\start_app.ps1"                      -ForegroundColor Yellow
Write-Host ""

# Start-Process so this terminal isn't blocked. Chrome stays running as
# long as you keep the window open.
Start-Process -FilePath $ChromePath -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=0.0.0.0",
    "--remote-allow-origins=*",
    "--user-data-dir=`"$UserDataDir`""
)

Write-Host "Chrome started. Verify from PowerShell:" -ForegroundColor Green
Write-Host "  Invoke-WebRequest http://localhost:$Port/json/version" -ForegroundColor Green
Write-Host ""
Write-Host "And from inside the container:"                     -ForegroundColor Green
Write-Host "  docker compose run --rm app python main.py --check-browser" -ForegroundColor Green
