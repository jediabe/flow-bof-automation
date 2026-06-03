# PyInstaller spec for Flow BOF Runner.
#
# One spec, two outputs depending on the build host:
#   - Windows : dist/FlowBOFRunner.exe (single-file console build)
#   - macOS   : dist/FlowBOFRunner.app (.app BUNDLE wrapping the binary)
#
# Build:
#   pyinstaller FlowBOFRunner.spec --clean --noconfirm
#
# Convenience wrappers:
#   - Windows : scripts/build_runner_windows.ps1
#   - macOS   : scripts/build_runner_mac.sh  (which also makes a .dmg)
#
# Bundling choices that stay constant across platforms:
#   - `console=True`. The packaged app is interactive (menu + stdin
#     prompts + Ctrl-C). A windowed build hides errors before the
#     user can read them.
#   - Playwright's browser binaries are NOT bundled. The runner
#     connects to the user's installed Chrome via CDP, so we only
#     ship the Playwright client library. That keeps the binary
#     under ~40 MB.
#   - Streamlit / AI SDKs / torch / tensorflow are excluded — the
#     runner doesn't import them, but PyInstaller's transitive
#     scan can pick them up if they're sitting in the venv.

# -*- mode: python ; coding: utf-8 -*-

import sys

from PyInstaller.utils.hooks import collect_submodules

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

block_cipher = None

# Force-include the src.runner_app modules + everything the existing
# automation code uses lazily. PyInstaller's static analysis catches
# direct imports but misses `import src.config` inside string-formed
# imports / late imports done from `handle_agent_job`.
hidden_imports = (
    collect_submodules("src")
    + collect_submodules("src.runner_app")
    + [
        # httpx + dependencies
        "httpx",
        "httpx._transports.default",
        # python-dotenv is loaded by src.config at import time
        "dotenv",
        # Playwright runtime. (`_impl._api_types` was removed in
        # newer Playwright builds; rely on collect_submodules above
        # to gather whatever the installed version actually ships.)
        "playwright",
        "playwright.sync_api",
    ]
)


a = Analysis(
    ["runner_app.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Streamlit + AI SDKs aren't part of the runner path. Listing
        # them here prevents PyInstaller from accidentally pulling
        # them in via a transitive import scan.
        "streamlit",
        "openai",
        "anthropic",
        "openpyxl",
        "streamlit_paste_button",
        "torch",
        "tensorflow",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FlowBOFRunner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX trips antivirus heuristics; not worth it
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,             # keep stderr / stdin visible
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


# macOS: wrap the binary in a .app bundle so Finder + Gatekeeper +
# `open` all behave correctly. The binary still lives at
# dist/FlowBOFRunner.app/Contents/MacOS/FlowBOFRunner and can be
# invoked directly from a Terminal (recommended for alpha because
# Finder-launched .apps don't show a console — see
# scripts/build_runner_mac.sh which also drops a
# "Run Flow BOF Runner.command" wrapper next to the .app).
if IS_MAC:
    app = BUNDLE(
        exe,
        name="FlowBOFRunner.app",
        # No custom icon yet. macOS shows the generic gear-and-cog
        # placeholder until we drop a .icns file in resources/.
        icon=None,
        bundle_identifier="xyz.autobof.flowbofrunner",
        info_plist={
            "CFBundleName":               "Flow BOF Runner",
            "CFBundleDisplayName":        "Flow BOF Runner",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion":            "0.1.0",
            # Required for proper rendering on Retina displays.
            "NSHighResolutionCapable":    True,
            # The runner never opens documents — keep the empty
            # CFBundleDocumentTypes default. We're also a "regular"
            # foreground-eligible app, not a UI element / agent,
            # so Finder shows us properly.
            "LSApplicationCategoryType":  "public.app-category.developer-tools",
            # macOS 11+ is plenty for alpha.
            "LSMinimumSystemVersion":     "11.0",
        },
    )
