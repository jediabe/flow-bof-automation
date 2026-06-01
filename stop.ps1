# stop.ps1 -- shut down the Docker services. Data is preserved.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Stopping Docker services..." -ForegroundColor Cyan
& docker compose down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] docker compose down exited with code $LASTEXITCODE." -ForegroundColor Yellow
}

# Optional: close the Chrome debug window.
$reply = Read-Host "Close all Chrome windows too? (y/N)"
if ($reply -eq "y" -or $reply -eq "Y") {
    Get-Process -Name chrome -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue
    Write-Host "[OK] Chrome closed." -ForegroundColor Green
}

Write-Host ""
Write-Host "Stopped. Your batches, settings, and API keys are preserved." -ForegroundColor Green
Write-Host "Run .\start.ps1 to resume." -ForegroundColor Green
