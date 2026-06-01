# setup.ps1 -- one-time installer for flow-bof-automation alpha.
# Runs from the unzipped folder root. Idempotent: safe to re-run.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Flow BOF Automation -- setup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify Docker Desktop is installed AND running.
try {
    $null = & docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker info exit $LASTEXITCODE" }
    Write-Host "[OK] Docker Desktop is running." -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Docker Desktop is not running." -ForegroundColor Red
    Write-Host ""
    Write-Host " Install: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
    Write-Host " Then start Docker Desktop and re-run setup.ps1." -ForegroundColor Yellow
    exit 1
}

# 2. Create the folders the app expects.
$folders = @(
    "data",
    "data\batches",
    "inputs",
    "inputs\reference_images",
    "inputs\incoming_images",
    "outputs",
    "outputs\images",
    "outputs\logs"
)
foreach ($f in $folders) {
    if (-not (Test-Path $f)) {
        New-Item -ItemType Directory -Path $f | Out-Null
        Write-Host "[+]  Created $f" -ForegroundColor Gray
    }
}
Write-Host "[OK] Project folders ready." -ForegroundColor Green

# 3. Copy .env.example -> .env if missing (only as a template;
#    real keys go through the UI Setup page, not this file).
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "[+]  Created .env from .env.example (template values)." -ForegroundColor Gray
    } else {
        Write-Host "[!]  No .env.example found; skipping .env bootstrap." -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] .env already exists; leaving it alone." -ForegroundColor Green
}

# 4. Build Docker images.
Write-Host ""
Write-Host "Building Docker images (this can take a few minutes the first time)..." -ForegroundColor Cyan
& docker compose build
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker compose build failed." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Images built." -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Setup complete. Next:" -ForegroundColor Green
Write-Host "   .\start.ps1" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
