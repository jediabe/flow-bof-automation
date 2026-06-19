# PyInstaller spec for Flow BOF Runner.
#
# One spec, two outputs depending on the build host:
#   - Windows : dist/FlowBOFRunner.exe (onefile, single ~44 MB exe)
#   - macOS   : dist/FlowBOFRunner.app (onedir .app bundle — the
#               .app's Contents/Frameworks/ holds the Python
#               interpreter + every dep as separate files, the
#               .app's Contents/MacOS/FlowBOFRunner is a small
#               launcher that loads them)
#
# Why two modes:
#   PyInstaller's onefile mode "works" for both platforms but
#   wrapping a self-extracting onefile binary INSIDE a .app bundle
#   conflicts with macOS Gatekeeper / SIP — Apple's tooling
#   expects to see a fully-laid-out .app, not a single binary that
#   later extracts to /tmp. PyInstaller 6 emits a deprecation
#   warning for that combination; PyInstaller 7 will refuse it.
#   onedir-then-BUNDLE is the supported macOS path. Windows
#   doesn't have an analogous bundle format, so onefile remains
#   the simplest distribution there.
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
#     user can read them. The Tk GUI launches via runner_app.py's
#     no-TTY auto-fallback when Finder double-clicks the .app.
#   - Playwright's browser binaries are NOT bundled. The runner
#     connects to the user's installed Chrome via CDP, so we only
#     ship the Playwright client library. That keeps the binary
#     under ~50 MB.
#   - Streamlit / AI SDKs / torch / tensorflow are excluded — the
#     runner doesn't import them, but PyInstaller's transitive
#     scan can pick them up if they're sitting in the venv.

# -*- mode: python ; coding: utf-8 -*-

import sys

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

block_cipher = None

# v0.6.15-alpha-3 — explicit data-file collection for playwright.
# We were relying on PyInstaller's built-in playwright hook to
# bundle site-packages/playwright/driver/ — but that hook
# either depends on the contrib package being current OR doesn't
# fire on every PyInstaller version. End-user rebuilt and still
# hit FileNotFoundError for the driver binary, so we're now
# explicitly collecting playwright's data files alongside the
# pyautogui chain. Belt-and-suspenders — harmless if the hook
# was already doing it.
#
# pyautogui's pyscreeze / mouseinfo subdeps ship platform binaries
# that PyInstaller's static scan misses. Bundle them so the os_native
# click path stays usable on the operator's machine.
runtime_datas = (
    collect_data_files("playwright")
    + collect_data_files("pyautogui")
    + collect_data_files("pyscreeze")
    + collect_data_files("mouseinfo")
)

# Force-include the src.runner_app modules + everything the existing
# automation code uses lazily. PyInstaller's static analysis catches
# direct imports but misses `import src.config` inside string-formed
# imports / late imports done from `handle_agent_job`.
hidden_imports = (
    collect_submodules("src")
    + collect_submodules("src.runner_app")
    # pyautogui has platform-conditional submodules
    # (_pyautogui_win / _x11 / _osx) that PyInstaller's static scan
    # misses, so collect them explicitly.
    + collect_submodules("pyautogui")
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
        # pyautogui runtime deps that don't show in __init__'s
        # imports — bundle explicitly so the os_native click path
        # works in the packaged exe.
        "pyautogui",
        "pymsgbox",
        "pytweening",
        "pyscreeze",
        "mouseinfo",
        # GUI module is at the top level (not under src.) and is
        # imported lazily inside `if args.gui:` / the interactive
        # menu — PyInstaller's static analysis misses lazy conditional
        # imports, so name it here so the GUI launches from the
        # packaged exe.
        "runner_gui",
        # Tkinter is stdlib but its native helpers (_tkinter,
        # tcl/tk runtime files) need explicit listing to bundle on
        # macOS — PyInstaller's tk hook handles the rest once the
        # top-level tkinter is in the import graph.
        "tkinter",
        "tkinter.ttk",
        "tkinter.font",
        "tkinter.messagebox",
    ]
)


a = Analysis(
    ["runner_app.py"],
    pathex=["."],
    binaries=[],
    datas=runtime_datas,
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


# Packaging mode differs by platform:
#
#   Windows: onefile — a single dist/FlowBOFRunner.exe that
#     self-extracts at launch. Easy distribution; familiar for end
#     users who download an .exe.
#
#   macOS:  onedir wrapped in a .app — the executable inside
#     Contents/MacOS/FlowBOFRunner plus Python interpreter +
#     stdlib + every dep live in Contents/Frameworks/. This is
#     what Apple's .app bundle format expects.
#     PyInstaller 7 will REQUIRE this; 6.x still permits the
#     legacy onefile+bundle combo but emits a deprecation warning.
#     The user never sees the difference — they still drag a
#     single .app icon to Applications.

if IS_MAC:
    # onedir mode: EXE references binaries via exclude_binaries=True,
    # then COLLECT gathers everything into a folder that BUNDLE
    # wraps as the .app.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="FlowBOFRunner",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=True,         # keep stderr / stdin visible for the
                              # .command launcher path; the GUI also
                              # works since it goes through the
                              # no-TTY fallback in runner_app.main()
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="FlowBOFRunner",
    )
    app = BUNDLE(
        coll,
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
            # macOS 11+ is plenty for alpha. Mirrors
            # MACOSX_DEPLOYMENT_TARGET=11.0 in build_runner_mac.sh.
            "LSMinimumSystemVersion":     "11.0",
        },
    )
else:
    # onefile mode for non-macOS (Windows / Linux): single
    # self-extracting binary.
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
