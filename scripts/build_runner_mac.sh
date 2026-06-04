#!/usr/bin/env bash
#
# Build Flow BOF Runner for macOS.
#
# Output:
#   dist/FlowBOFRunner.app
#   dist/Flow BOF Runner.command       (Terminal-launching wrapper)
#   dist/FlowBOFRunner-mac-alpha.dmg   (disk image with both of the above)
#
# Runs everything inside a repo-local .venv-runner, so the user's
# system Python install is untouched. No Docker required at any
# point. No Homebrew required (we use only macOS-builtin tools:
# python3, hdiutil).
#
# Requirements on the build machine:
#   - macOS (the script refuses to run elsewhere)
#   - Python 3.10+ (Python 3.11 or 3.12 strongly recommended; 3.14
#     currently lacks prebuilt greenlet/playwright wheels and falls
#     back to a source build that fails)
#   - Google Chrome installed (not bundled; the runner connects to
#     the user's installed Chrome via CDP)
#   - Xcode command-line tools (`xcode-select --install`) — pip needs
#     them when a wheel does miss and a source build is attempted

set -euo pipefail

# Sanity: we're on macOS.
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[FAIL] This script only runs on macOS. For Windows, use" >&2
  echo "       scripts/build_runner_windows.ps1." >&2
  exit 1
fi

# Land in the repo root regardless of where the script was invoked.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv-runner"
VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PYINST="$VENV_DIR/bin/pyinstaller"

DIST_DIR="$REPO_ROOT/dist"
APP_PATH="$DIST_DIR/FlowBOFRunner.app"
COMMAND_PATH="$DIST_DIR/Flow BOF Runner.command"
DMG_PATH="$DIST_DIR/FlowBOFRunner-mac-alpha.dmg"

echo
echo "============================================================"
echo " Flow BOF Runner -- macOS build"
echo "============================================================"
echo

# ---------------------------------------------------------------------
# 0. Pull latest code (skip with --no-pull)
# ---------------------------------------------------------------------
# Without this, repeat builds on a stale checkout silently produce an
# OLD runner. --ff-only refuses to merge or rebase so a user with
# uncommitted local changes gets a clear failure and can decide what
# to do. --no-pull skips the step for offline / detached-HEAD builds.
SKIP_PULL=false
for arg in "$@"; do
  if [[ "$arg" == "--no-pull" ]]; then
    SKIP_PULL=true
  fi
done

if [[ "$SKIP_PULL" == "true" ]]; then
  echo "Skipping git pull (--no-pull)."
elif [[ -d "$REPO_ROOT/.git" ]]; then
  echo "Pulling latest code (git pull --ff-only)..."
  if ! git -C "$REPO_ROOT" pull --ff-only; then
    echo
    echo "[FAIL] git pull --ff-only failed."
    echo "       Either commit/stash local changes, or re-run with"
    echo "       --no-pull to build off the current checkout."
    exit 1
  fi
else
  echo "Not a git checkout — skipping pull."
fi
echo

# ---------------------------------------------------------------------
# 1. Pick a Python (3.10..3.13 preferred; 3.14 sometimes lacks wheels)
# ---------------------------------------------------------------------
# Prefer the most wheel-friendly minor versions; fall back to whatever
# `python3` resolves to. `command -v` swallows the "not found" non-zero
# without aborting the script.
choose_python() {
  for cand in python3.12 python3.11 python3.10 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      ver=$("$cand" -c 'import sys; print("{0}.{1}".format(sys.version_info[0], sys.version_info[1]))' 2>/dev/null || true)
      # Need 3.10+. 3.0..3.9 reject; 3.14 accepted but warned about.
      maj=${ver%.*}
      min=${ver#*.}
      if [[ "$maj" == "3" && "$min" -ge 10 ]]; then
        echo "$cand"
        return 0
      fi
    fi
  done
  return 1
}

# Validate any pre-existing venv's Python version. If it's < 3.10
# (typically Apple's bundled 3.9.6 picked up on a first build before
# Python 3.12 was installed), nuke it and recreate — otherwise the
# script silently reuses a Python that can't build a working
# Tkinter on macOS Tahoe 26+ (Apple's bundled Tk is frozen at a
# pre-Tahoe version and bails with "macOS 26 (2602) required, have
# instead 16 (1602)" when the .app's GUI launches).
if [[ -x "$VENV_PY" ]]; then
  EXISTING_VER=$("$VENV_PY" -c 'import sys; print("{0}.{1}".format(sys.version_info[0], sys.version_info[1]))' 2>/dev/null || echo "0.0")
  EX_MAJ=${EXISTING_VER%.*}
  EX_MIN=${EXISTING_VER#*.}
  if [[ "$EX_MAJ" != "3" || "$EX_MIN" -lt 10 ]]; then
    echo "[WARN] Existing venv has Python $EXISTING_VER (< 3.10). Recreating."
    echo "       (Apple's system Python 3.9.6 ships a frozen Tk that"
    echo "       doesn't support macOS Tahoe; the .app GUI silently"
    echo "       refuses to launch.)"
    rm -rf "$VENV_DIR"
  fi
fi

if [[ ! -x "$VENV_PY" ]]; then
  PY=$(choose_python || true)
  if [[ -z "${PY:-}" ]]; then
    echo "[FAIL] No suitable Python 3.10+ found." >&2
    echo "       Install from python.org or:  brew install python@3.12" >&2
    exit 1
  fi
  PY_VER=$("$PY" -c 'import sys; print("{0}.{1}".format(sys.version_info[0], sys.version_info[1]))')
  case "$PY_VER" in
    3.10|3.11|3.12) ;;
    *)
      echo "[WARN] Building with Python $PY_VER. Some native deps"
      echo "       (greenlet via playwright) may lack wheels for this"
      echo "       version and force a source build."
      echo "       If the next step fails with a compiler error, install"
      echo "       Python 3.12 and re-run this script."
      ;;
  esac
  echo "Creating venv at $VENV_DIR (using $PY $PY_VER)..."
  "$PY" -m venv "$VENV_DIR"
else
  EXISTING_VER=$("$VENV_PY" -c 'import sys; print("{0}.{1}".format(sys.version_info[0], sys.version_info[1]))' 2>/dev/null || echo "?")
  echo "Reusing existing venv at $VENV_DIR (Python $EXISTING_VER)."
fi

# Also force MACOSX_DEPLOYMENT_TARGET=11.0 unless the caller has
# already set one. This keeps the bundled .app loadable on every
# macOS from Big Sur (Nov 2020) forward — without it, pip wheels
# can stamp a too-new minos that makes the .app refuse to load on
# older Macs your end users may have.
if [[ -z "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
  export MACOSX_DEPLOYMENT_TARGET=11.0
  echo "MACOSX_DEPLOYMENT_TARGET defaulted to 11.0 (Big Sur)."
fi

# ---------------------------------------------------------------------
# 2. Install deps
# ---------------------------------------------------------------------
# Pin pip to <26. pip 26.x (released late 2025) shipped a broken
# bundled distlib that crashes on import inside ensurepip on
# Homebrew Python and on macOS Tahoe in general. Whatever the venv
# already has from `python -m venv` is usually 25.x and works fine,
# but `--upgrade pip` would yank the latest, including the broken
# 26.x. The constraint keeps us on the stable 25.x line until pip
# 26.x is fixed.
echo "Pinning pip to <26..."
"$VENV_PY" -m pip install --quiet "pip<26"

echo "Installing requirements-runner.txt..."
"$VENV_PIP" install -r "$REPO_ROOT/requirements-runner.txt"

# ---------------------------------------------------------------------
# 3. PyInstaller
# ---------------------------------------------------------------------
echo "Running PyInstaller..."
# --clean nukes PyInstaller's intermediate build/ cache so the spec
# file's `excludes` actually take effect on a rebuild.
# --noconfirm overwrites any existing dist/FlowBOFRunner.app without
# asking.
"$VENV_PYINST" FlowBOFRunner.spec --clean --noconfirm

if [[ ! -d "$APP_PATH" ]]; then
  echo "[FAIL] PyInstaller did not produce $APP_PATH. Check the build log."
  exit 1
fi

# ---------------------------------------------------------------------
# 4. Terminal-launching .command wrapper
# ---------------------------------------------------------------------
# macOS double-click on a .app silently exec's the inner binary —
# no console window appears, so the user can't see the runner's
# stdin prompts. Workaround: ship a `.command` file alongside the
# .app that opens Terminal and runs the binary. macOS treats
# `.command` files as "open in Terminal on double-click".
echo "Writing Terminal wrapper..."
cat > "$COMMAND_PATH" <<'EOF'
#!/usr/bin/env bash
# Double-clicking this file opens a Terminal window and starts the
# Flow BOF Runner inside it, so you can see status + paste your
# runner token. Mirrors how `Run as administrator` works on Windows.
set -e
# Resolve next to this script. Works whether the user dragged the
# folder anywhere or runs from inside the dmg.
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/FlowBOFRunner.app/Contents/MacOS/FlowBOFRunner"
if [[ ! -x "$BIN" ]]; then
  echo "Error: FlowBOFRunner.app is not next to this Run file."
  echo "Drag both into the same folder (e.g. /Applications) and try again."
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi
exec "$BIN" "$@"
EOF
chmod +x "$COMMAND_PATH"

# ---------------------------------------------------------------------
# 5. DMG
# ---------------------------------------------------------------------
# Build a UDZO-compressed disk image containing the .app + the
# .command wrapper. The user mounts it, drags the .app to
# /Applications, optionally drags the .command file to the desktop
# (or always launches from inside the mounted dmg).
if command -v hdiutil >/dev/null 2>&1; then
  echo "Building DMG with hdiutil..."

  # Stage the dmg contents in a tmpdir so the user doesn't see
  # other random dist/ artefacts inside the mounted image.
  STAGE_DIR=$(mktemp -d -t flowbof_dmg)
  trap 'rm -rf "$STAGE_DIR"' EXIT
  cp -R "$APP_PATH" "$STAGE_DIR/"
  cp "$COMMAND_PATH" "$STAGE_DIR/"

  # -ov overwrites any existing dmg. UDZO is the standard read-only
  # compressed format Finder mounts natively.
  rm -f "$DMG_PATH"
  hdiutil create \
    -volname "Flow BOF Runner" \
    -srcfolder "$STAGE_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH" >/dev/null
else
  echo "[WARN] hdiutil not available; skipping dmg. The .app and .command"
  echo "       file are usable as-is."
fi

# ---------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------
echo
echo "============================================================"
echo " Build complete."
echo "============================================================"
APP_SIZE=$(du -sh "$APP_PATH" 2>/dev/null | awk '{print $1}')
echo "  app  : $APP_PATH  ($APP_SIZE)"
if [[ -f "$DMG_PATH" ]]; then
  DMG_SIZE=$(du -sh "$DMG_PATH" 2>/dev/null | awk '{print $1}')
  echo "  dmg  : $DMG_PATH  ($DMG_SIZE)"
fi
echo "  run  : $COMMAND_PATH"
echo
echo " The .app is UNSIGNED. First-launch on a tester's Mac will"
echo " trigger Gatekeeper:"
echo "   right-click the .app -> Open -> Open"
echo " or System Settings -> Privacy & Security -> Open Anyway."
echo
echo " Quick check on this machine:"
echo "   $APP_PATH/Contents/MacOS/FlowBOFRunner --diagnose"
echo
