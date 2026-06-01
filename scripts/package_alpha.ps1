# package_alpha.ps1 -- build a private alpha ZIP that another tester can
# unzip and run. The output goes to ./dist/.
#
# Excludes anything secret, ephemeral, or heavy. See docs/DISTRIBUTION.md
# for the full inclusion/exclusion rules.

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$date = Get-Date -Format "yyyyMMdd"
$distDir = Join-Path $Root "dist"
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

$zipName = "flow-bof-automation-alpha-$date.zip"
$zipPath = Join-Path $distDir $zipName
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
    Write-Host "[+] Removed pre-existing $zipName" -ForegroundColor Gray
}

# Stage to a fresh subdir so robocopy's exclusion semantics work cleanly.
$staging = Join-Path $distDir "_staging"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null

# Top-level files we want in the package.
$includeFiles = @(
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "README_FIRST.md",
    "README.md",
    "requirements.txt",
    "main.py",
    "streamlit_app.py",
    "setup.ps1",
    "start.ps1",
    "stop.ps1",
    "reset.ps1",
    # macOS lifecycle scripts — shipped alongside the .ps1 versions so a
    # single ZIP works on both platforms. The .sh executable bits need
    # to be re-set by the Mac tester (the ZIP format loses them on some
    # extraction tools), which is exactly what setup.sh does.
    "setup.sh",
    "start.sh",
    "stop.sh"
)
foreach ($f in $includeFiles) {
    if (Test-Path $f) {
        Copy-Item $f -Destination (Join-Path $staging $f)
    }
}

# Source / config directories we want.
$includeDirs = @("src", "ai", "scripts", "docker", "docs")
foreach ($d in $includeDirs) {
    if (Test-Path $d) {
        $dest = Join-Path $staging $d
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        # /MIR = mirror, /NFL /NDL /NJH /NJS = quiet output.
        & robocopy $d $dest /E `
            /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache `
            /XF *.pyc *.tmp `
            /NFL /NDL /NJH /NJS | Out-Null
    }
}

# Empty-skeleton dirs the runtime needs at startup.
$skeletons = @(
    "inputs",
    "inputs\reference_images",
    "inputs\incoming_images",
    "outputs",
    "outputs\images",
    "outputs\logs",
    "data",
    "data\batches"
)
foreach ($d in $skeletons) {
    $dest = Join-Path $staging $d
    if (-not (Test-Path $dest)) {
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
    }
    # Drop a .gitkeep so the empty dir survives if the user re-zips it later.
    $keep = Join-Path $dest ".gitkeep"
    if (-not (Test-Path $keep)) { New-Item -ItemType File -Path $keep | Out-Null }
}

# --- Belt-and-suspenders exclusions on the staged copy ---------------
# robocopy can leave files behind that we definitely don't want shipped.
$dropPaths = @(
    "data\secrets.local.json",
    "data\settings.local.json",
    "data\unmatched_favorites.json",
    ".env",
    "inputs\products.csv",
    "inputs\prompt_manifest.md"
)
foreach ($p in $dropPaths) {
    $abs = Join-Path $staging $p
    if (Test-Path $abs) {
        Remove-Item $abs -Force -Recurse
        Write-Host "[-] Excluded $p" -ForegroundColor Gray
    }
}
# Wildcard cleanups (backup CSVs, log files, image binaries).
Get-ChildItem (Join-Path $staging "inputs") -Filter "products.csv.*" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $staging "outputs\logs") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne ".gitkeep" } | Remove-Item -Force
Get-ChildItem (Join-Path $staging "outputs\images") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne ".gitkeep" } | Remove-Item -Force
# Reference images are user-specific; ship empty.
Get-ChildItem (Join-Path $staging "inputs\reference_images") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne ".gitkeep" } | Remove-Item -Force
Get-ChildItem (Join-Path $staging "inputs\incoming_images") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne ".gitkeep" } | Remove-Item -Force
# data/batches should ship empty for testers.
Get-ChildItem (Join-Path $staging "data\batches") -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Final paranoia check: error if any obviously-secret file remains.
$leakWatch = @("secrets.local.json", ".env")
foreach ($lw in $leakWatch) {
    $hits = Get-ChildItem $staging -Recurse -Filter $lw -ErrorAction SilentlyContinue
    if ($hits) {
        Write-Host "[FAIL] $lw found in staging:" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host "       $($_.FullName)" -ForegroundColor Red }
        exit 1
    }
}

# Zip it.
Write-Host "Compressing..." -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zipPath -Force
Remove-Item $staging -Recurse -Force

Write-Host ""
Write-Host "Alpha package created at $zipPath" -ForegroundColor Green
$size = (Get-Item $zipPath).Length
Write-Host ("Size: {0:N1} MB" -f ($size / 1MB)) -ForegroundColor Gray
