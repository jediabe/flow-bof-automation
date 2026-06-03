"""Runner config — load/save runner_config.json + first-time prompt.

The single source of truth at runtime is the `RunnerConfig` dataclass
below. The on-disk JSON is the same shape, modulo path normalisation.

We deliberately keep the file format trivial JSON instead of YAML /
TOML so an end user can hand-edit it from Notepad if they ever need
to — and so PyInstaller doesn't have to bundle a parser.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path  # noqa: F401  (re-exported for runner_app.py)
from typing import Optional

from .paths import (
    default_chrome_profile_dir,
    ensure_dir,
    runner_config_path,
    runner_data_dir,
)


DEFAULT_SAAS_URL = "https://app.autobof.xyz"
# Google Flow's current entry point. The runner only navigates here
# at startup if no Flow tab exists in the dedicated profile yet;
# subsequent runs reuse whatever tab the user has open.
DEFAULT_FLOW_URL = "https://labs.google/fx/tools/flow"
DEFAULT_CHROME_PORT = 9222


@dataclass
class RunnerConfig:
    saas_base_url: str = DEFAULT_SAAS_URL
    runner_token: str = ""
    poll_interval_seconds: float = 5.0
    health_interval_seconds: float = 30.0
    http_timeout_seconds: float = 30.0
    chrome_debug_port: int = DEFAULT_CHROME_PORT
    chrome_profile_dir: str = field(default_factory=lambda: str(default_chrome_profile_dir()))
    flow_url: str = DEFAULT_FLOW_URL

    def token_last4(self) -> str:
        t = (self.runner_token or "").strip()
        if not t:
            return ""
        return t[-4:] if len(t) > 4 else t

    def token_masked(self) -> str:
        l4 = self.token_last4()
        return f"runner_****{l4}" if l4 else "(not set)"

    def is_ready_to_run(self) -> bool:
        return bool(self.saas_base_url.strip()) and bool(self.runner_token.strip())


def load_config() -> RunnerConfig:
    """Read runner_config.json into a RunnerConfig. Returns the
    default-shaped config when the file doesn't exist; corrupted JSON
    is treated the same way (overwriting on next save is fine for
    alpha — no migration logic yet)."""
    path = runner_config_path()
    if not path.exists():
        return RunnerConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RunnerConfig()
    if not isinstance(raw, dict):
        return RunnerConfig()
    base = RunnerConfig()
    return RunnerConfig(
        saas_base_url=str(raw.get("saasBaseUrl") or base.saas_base_url).strip()
            or base.saas_base_url,
        runner_token=str(raw.get("runnerToken") or "").strip(),
        poll_interval_seconds=float(raw.get("pollIntervalSeconds") or base.poll_interval_seconds),
        health_interval_seconds=float(raw.get("healthIntervalSeconds") or base.health_interval_seconds),
        http_timeout_seconds=float(raw.get("httpTimeoutSeconds") or base.http_timeout_seconds),
        chrome_debug_port=int(raw.get("chromeDebugPort") or base.chrome_debug_port),
        chrome_profile_dir=str(raw.get("chromeProfileDir") or base.chrome_profile_dir),
        flow_url=str(raw.get("flowUrl") or base.flow_url),
    )


def save_config(cfg: RunnerConfig) -> Path:
    """Persist the config. Creates the data directory if missing.
    Sets owner-only permissions on POSIX so the token isn't world-
    readable; Windows ACLs require pywin32 to twiddle so we leave the
    default and document the trade-off in RUNNER_PACKAGING.md."""
    path = runner_config_path()
    ensure_dir(path.parent)
    serialised = {
        "saasBaseUrl": cfg.saas_base_url.strip(),
        "runnerToken": cfg.runner_token.strip(),
        "pollIntervalSeconds": float(cfg.poll_interval_seconds),
        "healthIntervalSeconds": float(cfg.health_interval_seconds),
        "httpTimeoutSeconds": float(cfg.http_timeout_seconds),
        "chromeDebugPort": int(cfg.chrome_debug_port),
        "chromeProfileDir": str(cfg.chrome_profile_dir),
        "flowUrl": cfg.flow_url.strip(),
    }
    path.write_text(json.dumps(serialised, indent=2), encoding="utf-8")
    # Owner-only read on POSIX (0600). Windows is left at default
    # because chmod has no effect there and twiddling ACLs would
    # require an extra dependency we don't want in the exe.
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


def reset_config() -> bool:
    """Delete runner_config.json. Returns True if a file was removed."""
    path = runner_config_path()
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------------
# First-run prompt
# ---------------------------------------------------------------------

def _input(prompt: str, default: str = "") -> str:
    """input() with a one-shot default. Returns the trimmed value."""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    try:
        v = input(prompt).strip()
    except EOFError:
        return default
    return v or default


def prompt_for_missing(cfg: RunnerConfig) -> RunnerConfig:
    """Interactively fill in any required field that's still empty.

    Currently the only mandatory pair is saas_base_url + runner_token;
    everything else has a sensible default. The runner token is read
    with `input()` (visible) on purpose — it's a non-secret-ish
    bearer token the user just pasted from their browser, and getpass
    fails in the bundled exe on Windows for unrelated reasons.
    """
    saas_url = cfg.saas_base_url
    if not saas_url.strip():
        saas_url = _input("SaaS URL", DEFAULT_SAAS_URL)
    token = cfg.runner_token
    if not token.strip():
        print()
        print("Generate a runner token at:")
        print(f"  {saas_url.rstrip('/')}/agents")
        print("(Sign in → register a runner → Generate runner token.)")
        print()
        while not token.strip():
            token = _input("Paste runner token", "")
            if not token.strip():
                print("Token is required.")
    return replace(cfg, saas_base_url=saas_url.strip(), runner_token=token.strip())


def replace_via_prompt_if_explicit(cfg: RunnerConfig) -> RunnerConfig:
    """Re-prompt for SaaS URL + token even when both are already
    populated. Called only from the explicit --setup / "Re-enter…"
    menu path so we don't badger the user on every startup."""
    new_url = _input("SaaS URL", cfg.saas_base_url or DEFAULT_SAAS_URL)
    print(
        "Paste a new runner token to replace the saved one, or press "
        f"Enter to keep the current ({cfg.token_masked()})."
    )
    new_token = _input("Runner token", "")
    return replace(
        cfg,
        saas_base_url=new_url.strip() or cfg.saas_base_url,
        runner_token=new_token.strip() or cfg.runner_token,
    )


def profile_dir_path(cfg: RunnerConfig) -> Path:
    """Coerce the config's stringly-typed profile dir into a Path.
    Callers further down don't have to repeat the conversion."""
    return Path(cfg.chrome_profile_dir)


def print_summary(cfg: RunnerConfig) -> None:
    """Operator-visible summary. Token is always masked."""
    print()
    print("Flow BOF Runner — current configuration")
    print(f"  SaaS URL          : {cfg.saas_base_url}")
    print(f"  Runner token      : {cfg.token_masked()}")
    print(f"  Chrome debug port : {cfg.chrome_debug_port}")
    print(f"  Chrome profile    : {cfg.chrome_profile_dir}")
    print(f"  Flow URL          : {cfg.flow_url}")
    print(f"  Poll interval     : {cfg.poll_interval_seconds}s")
    print(f"  Config file       : {runner_config_path()}")
    print()
