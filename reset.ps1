# reset.ps1 -- clear runtime state so you can start a fresh batch run.
# Preserves: data/batches/* (your products and prompts), settings, keys.
# Clears:   outputs/logs/*, inputs/products.csv, inputs/prompt_manifest.md,
#           data/unmatched_favorites.json.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host " RESET will:" -ForegroundColor Yellow
Write-Host "   - Stop running containers" -ForegroundColor Yellow
Write-Host "   - Clear outputs/logs" -ForegroundColor Yellow
Write-Host "   - Delete inputs/products.csv and prompt_manifest.md" -ForegroundColor Yellow
Write-Host "   - Delete data/unmatched_favorites.json" -ForegroundColor Yellow
Write-Host ""
Write-Host " RESET will NOT touch:" -ForegroundColor Cyan
Write-Host "   - data/batches/*       (your product cards stay)" -ForegroundColor Cyan
Write-Host "   - data/settings.local.json / data/secrets.local.json (your AI key stays)" -ForegroundColor Cyan
Write-Host "   - inputs/reference_images" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host ""

$reply = Read-Host "Continue? (y/N)"
if ($reply -ne "y" -and $reply -ne "Y") {
    Write-Host "Cancelled." -ForegroundColor Gray
    exit 0
}

& docker compose down --remove-orphans | Out-Null

$paths = @(
    "outputs\logs\*",
    "inputs\products.csv",
    "inputs\prompt_manifest.md",
    "data\unmatched_favorites.json"
)
foreach ($p in $paths) {
    if (Test-Path $p) {
        Remove-Item $p -Force -Recurse -ErrorAction SilentlyContinue
        Write-Host "[+] Removed $p" -ForegroundColor Gray
    }
}

# Also clean leftover backup/temp CSVs Excel-locking sometimes leaves.
Get-ChildItem "inputs" -Filter "products.csv.bak.*" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem "inputs" -Filter "products.csv.*.tmp" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Reset complete. Run .\start.ps1 to begin a fresh batch." -ForegroundColor Green
