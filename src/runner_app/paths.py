"""Cross-platform paths for the standalone runner.

We deliberately stay out of the repo tree so the same config survives
an upgrade-by-replacing-the-exe. Windows uses %APPDATA%, macOS uses
~/Library/Application Support, Linux uses ~/.config — the user's
native conventions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME_DIR = "FlowBOF"


def runner_data_dir() -> Path:
    """Where runner_config.json and any future runner state lives.

    Per-platform native locations:
      Windows: %APPDATA%\\FlowBOF
      macOS:   ~/Library/Application Support/FlowBOF
      Linux:   ~/.config/FlowBOF
    """
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_NAME_DIR
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME_DIR
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / APP_NAME_DIR.lower().replace(" ", "-")


def runner_config_path() -> Path:
    return runner_data_dir() / "runner_config.json"


def default_chrome_profile_dir() -> Path:
    """Where the dedicated FlowBOF Chrome profile lives.

    Always a separate directory from the user's normal Chrome — the
    runner never touches their daily browsing profile. On Windows we
    drop it under LOCALAPPDATA so it survives roaming-profile copies
    cleanly; on other platforms a sibling of the config dir is fine.
    """
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(local) / APP_NAME_DIR / "ChromeProfile"
    return runner_data_dir() / "chrome-profile"


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
