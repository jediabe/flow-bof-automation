# Build FlowBOFRunner.exe on Windows.
#
# What this does, in order:
#   1. Create / reuse a `.venv-runner` virtualenv next to the repo.
#   2. Upgrade pip in that venv.
#   3. Install requirements-runner.txt (lean -- no Streamlit / AI SDKs).
#   4. Run PyInstaller against FlowBOFRunner.spec.
#   5. Print where the exe ended up.
#
# Run from the repo root:
#
#   .\scripts\build_runner_windows.ps1
#
# Output:
#   dist\FlowBOFRunner.exe
#
# Does NOT require Docker. Does NOT touch the user's system Python
# install -- everything lives in `.venv-runner`. Re-runs are
# idempotent; the spec file controls what ends up in the exe.

# Native CLIs (pip, pyinstaller, py.exe) routinely write progress
# and info-level lines to STDERR. PowerShell's "Stop" preference
# treats any stderr write as a terminating error, which would abort
# this build halfway through a legitimate run. We use "Continue" at
# the script level and check `$LASTEXITCODE` explicitly after each
# native call — that's what actually reflects success / failure.
$ErrorActionPreference = "Continue"

# Land in the repo root regardless of where the script was invoked.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvDir   = Join-Path $RepoRoot ".venv-runner"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPyInst = Join-Path $VenvDir "Scripts\pyinstaller.exe"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Flow BOF Runner -- Windows build" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Find a Python to bootstrap the venv. Prefer the `py` launcher
#    (it knows about every installed Python); fall back to `python`
#    on PATH. We need 3.10+ for the typing syntax used in
#    src/runner_app.
#
# Robustness note: `py -3.12` (or any specific minor that isn't
# installed) writes "No suitable Python runtime found" to stderr and
# exits non-zero. With the script's outer $ErrorActionPreference=Stop
# in effect, PowerShell can promote that to a terminating error even
# with `2>$null` redirection. We isolate every probe in a
# Continue-mode try/catch + restore the global preference afterwards.
function Try-Python {
    param([string]$Launcher, [string[]]$LauncherArgs)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Launcher @LauncherArgs -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $line = ($out | Select-Object -First 1).ToString().Trim()
            if ($line -and (Test-Path $line)) { return $line }
        }
    } catch {
        # Swallow -- failed probe just means "this Python isn't installed."
    } finally {
        $ErrorActionPreference = $saved
    }
    return $null
}

# Search order: prefer Python minors with the broadest *wheel*
# coverage for the runner's native deps (greenlet via playwright,
# etc.). Python 3.14 was released October 2025 and several
# C-extension packages don't ship wheels for it yet -- pip then tries
# to build from source and chokes on internal-API renames like
# Py_C_RECURSION_LIMIT. 3.12 is the most reliable sweet spot today;
# 3.11 and 3.10 are also fine.
#
# We still accept 3.13 / 3.14 as a last resort and surface a warning
# explaining the wheel risk.
$PYTHON_SEARCH_ORDER = @("-3.12", "-3.11", "-3.10", "-3.13", "-3.14", "-3")
$PYTHON_WHEEL_FRIENDLY = @("3.10", "3.11", "3.12")

function Get-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in $PYTHON_SEARCH_ORDER) {
            $found = Try-Python "py" @($v)
            if ($found) { return $found }
        }
    }
    # Fallback: bare `python` on PATH. Accept only if it's 3.10+.
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $found = Try-Python "python" @()
        if ($found) {
            $saved = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $ver = & python -c "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))" 2>$null
            } finally {
                $ErrorActionPreference = $saved
            }
            if ($LASTEXITCODE -eq 0 -and $ver) {
                $parts = $ver.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                    return $found
                }
            }
        }
    }
    return $null
}

# Read the venv's Python version (X.Y). Used to warn / pivot when
# the venv ended up on a wheel-unfriendly minor.
function Get-VenvPythonMinor {
    param([string]$VenvPyPath)
    if (-not (Test-Path $VenvPyPath)) { return $null }
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $v = & $VenvPyPath -c "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))" 2>$null
    } finally {
        $ErrorActionPreference = $saved
    }
    if ($LASTEXITCODE -eq 0 -and $v) { return $v.Trim() }
    return $null
}

# If a venv already exists, check whether it landed on a Python
# version that won't build cleanly. We rebuild automatically so the
# user doesn't have to know about the `Py_C_RECURSION_LIMIT` greenlet
# failure mode. The deletion is safe -- .venv-runner is repo-local
# and contains no user data.
if (Test-Path $VenvPython) {
    $existingMinor = Get-VenvPythonMinor $VenvPython
    if ($existingMinor -and ($PYTHON_WHEEL_FRIENDLY -notcontains $existingMinor)) {
        Write-Host ""
        Write-Host "[WARN] Existing venv is on Python $existingMinor, which lacks " -ForegroundColor Yellow -NoNewline
        Write-Host "prebuilt wheels for greenlet/playwright." -ForegroundColor Yellow
        Write-Host "       Rebuilding the venv against a wheel-friendly Python..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $VenvDir
    }
}

if (-not (Test-Path $VenvPython)) {
    $py = Get-Python
    if (-not $py) {
        Write-Host ""
        Write-Host "[FAIL] No Python 3.10+ found. Install Python 3.12 from python.org" -ForegroundColor Red
        Write-Host "       (it has the best wheel coverage for the runner's native deps)." -ForegroundColor Red
        Write-Host "       Direct link: https://www.python.org/downloads/release/python-3127/" -ForegroundColor Red
        exit 1
    }

    # Surface a warning if the only Python we found is on the
    # bleeding edge. The user can still try -- newer wheels do
    # land -- but they shouldn't be surprised when greenlet etc.
    # fall back to a source build.
    $minor = $null
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $minor = (& $py -c "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))" 2>$null).Trim()
    } finally {
        $ErrorActionPreference = $saved
    }
    if ($minor -and ($PYTHON_WHEEL_FRIENDLY -notcontains $minor)) {
        Write-Host ""
        Write-Host "[WARN] Only Python $minor is available." -ForegroundColor Yellow
        Write-Host "       Several native deps (greenlet via playwright) may not have" -ForegroundColor Yellow
        Write-Host "       wheels for this version yet, in which case pip will try a" -ForegroundColor Yellow
        Write-Host "       source build and fail with 'Py_C_RECURSION_LIMIT undeclared'." -ForegroundColor Yellow
        Write-Host "       Recommended: install Python 3.12 from python.org and re-run." -ForegroundColor Yellow
        Write-Host ""
    }

    Write-Host "Creating venv at $VenvDir (using $py)..." -ForegroundColor Cyan
    & $py -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] venv creation failed (exit $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Reusing existing venv at $VenvDir." -ForegroundColor Green
}

# 2. Upgrade pip.
Write-Host "Upgrading pip..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip | Out-Null

# 3. Install runner deps.
Write-Host "Installing requirements-runner.txt..." -ForegroundColor Cyan
& $VenvPip install -r (Join-Path $RepoRoot "requirements-runner.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[FAIL] pip install failed (exit $LASTEXITCODE)." -ForegroundColor Red
    # 95% of the time this is the greenlet-on-bleeding-edge-Python
    # case. Surface the cause + the fix without making the user
    # search the build log.
    $venvMinor = Get-VenvPythonMinor $VenvPython
    if ($venvMinor -and ($PYTHON_WHEEL_FRIENDLY -notcontains $venvMinor)) {
        Write-Host ""
        Write-Host "       The venv is on Python $venvMinor -- too new for the prebuilt" -ForegroundColor Yellow
        Write-Host "       wheels of greenlet / playwright. pip tried a source build" -ForegroundColor Yellow
        Write-Host "       and hit MSVC errors like 'Py_C_RECURSION_LIMIT undeclared'." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "       Fix: install Python 3.12 from python.org, then re-run this" -ForegroundColor Yellow
        Write-Host "       script. It will detect the new Python, delete the stale" -ForegroundColor Yellow
        Write-Host "       .venv-runner, and rebuild against 3.12 automatically." -ForegroundColor Yellow
        Write-Host "       Direct link: https://www.python.org/downloads/release/python-3127/" -ForegroundColor Yellow
    }
    exit 1
}

# 4. PyInstaller.
Write-Host "Running PyInstaller..." -ForegroundColor Cyan
# `--clean` drops PyInstaller's intermediate build/ cache so the spec
# file's `excludes` actually take effect on a rebuild. `--noconfirm`
# overwrites any existing dist/FlowBOFRunner.exe without asking.
& $VenvPyInst FlowBOFRunner.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] PyInstaller failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

# 5. Report.
$ExePath = Join-Path $RepoRoot "dist\FlowBOFRunner.exe"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Build complete." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
if (Test-Path $ExePath) {
    $size = (Get-Item $ExePath).Length / 1MB
    Write-Host ("  exe : {0}  ({1:N1} MB)" -f $ExePath, $size) -ForegroundColor Green
} else {
    Write-Host "  exe : not found at $ExePath" -ForegroundColor Yellow
    Write-Host "        check PyInstaller output above." -ForegroundColor Yellow
    exit 1
}
Write-Host ""
Write-Host " Test it from another PowerShell:" -ForegroundColor Green
Write-Host "   .\dist\FlowBOFRunner.exe --diagnose" -ForegroundColor Green
Write-Host ""
