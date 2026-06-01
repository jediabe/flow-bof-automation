# Stop any running compose services and remove their containers.
# Useful when something hung; the normal `docker compose run --rm`
# pattern auto-cleans containers, so this is mainly a courtesy.

$ErrorActionPreference = "Stop"

Write-Host "Stopping flow-bof-automation containers..." -ForegroundColor Cyan
docker compose down --remove-orphans
Write-Host "Done." -ForegroundColor Green
