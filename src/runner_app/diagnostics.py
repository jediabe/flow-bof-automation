"""`runner_app.py --diagnose` — read-only end-to-end check.

Each step prints `[OK]` / `[WARN]` / `[FAIL]` with a one-liner so a
user can paste the whole output into an issue and we can tell which
layer is broken without follow-up questions.

The checks are intentionally sequential and short-circuit-free —
we want to see EVERY result, not just the first failure. Failure of
one step doesn't abort the next.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Iterable

import httpx

from .chrome import (
    cdp_url,
    cdp_version_url,
    find_chrome,
    is_cdp_reachable,
    platform_label,
)
from .config import RunnerConfig, load_config
from .paths import runner_config_path


logger = logging.getLogger("runner_app.diagnose")


# ---------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------

class Result:
    """Tiny holder for one diagnostic step's outcome."""

    __slots__ = ("level", "label", "detail", "hint")

    def __init__(
        self,
        level: str,
        label: str,
        detail: str = "",
        hint: str = "",
    ) -> None:
        self.level = level
        self.label = label
        self.detail = detail
        self.hint = hint

    def render(self) -> str:
        head = f"[{self.level:<4}] {self.label}"
        if self.detail:
            head = f"{head} — {self.detail}"
        if self.hint:
            head = f"{head}\n         hint: {self.hint}"
        return head


def ok(label: str, detail: str = "") -> Result:
    return Result("OK", label, detail)


def warn(label: str, detail: str = "", hint: str = "") -> Result:
    return Result("WARN", label, detail, hint)


def fail(label: str, detail: str = "", hint: str = "") -> Result:
    return Result("FAIL", label, detail, hint)


# ---------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------

def check_config_file() -> tuple[Result, RunnerConfig]:
    path = runner_config_path()
    if not path.exists():
        cfg = load_config()  # returns defaults
        return (
            warn(
                "config file",
                f"missing at {path}",
                "run the runner once without --diagnose to create it.",
            ),
            cfg,
        )
    cfg = load_config()
    return ok("config file", str(path)), cfg


def check_saas_health(cfg: RunnerConfig) -> Result:
    if not cfg.saas_base_url.strip():
        return fail("SaaS reachable", "saas_base_url is empty")
    url = cfg.saas_base_url.rstrip("/") + "/api/health"
    try:
        with httpx.Client(timeout=cfg.http_timeout_seconds) as c:
            r = c.get(url)
    except httpx.HTTPError as exc:
        return fail(
            "SaaS reachable",
            f"{type(exc).__name__}: {exc}",
            "double-check the URL spelling + that the host is up.",
        )
    if r.status_code != 200:
        return fail(
            "SaaS reachable",
            f"{url} returned {r.status_code}",
        )
    try:
        body = r.json()
    except ValueError:
        return warn(
            "SaaS reachable",
            f"{url} answered 200 but body wasn't JSON",
        )
    db = body.get("database") if isinstance(body, dict) else None
    return ok("SaaS reachable", f"version={body.get('version')} database={db}")


def check_runner_token(cfg: RunnerConfig) -> Result:
    if not cfg.runner_token.strip():
        return fail(
            "runner token",
            "no token saved",
            "open Runner Setup in the SaaS, generate a token, paste it here.",
        )
    if not cfg.runner_token.startswith("runner_"):
        return warn(
            "runner token",
            "doesn't start with 'runner_' — may have been pasted incorrectly",
        )

    headers = {
        "Authorization": f"Bearer {cfg.runner_token.strip()}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    body = {
        "runnerVersion": "diagnose",
        "platform": platform_label(),
        "capabilities": [],
    }
    url = cfg.saas_base_url.rstrip("/") + "/api/runner/health"
    try:
        with httpx.Client(timeout=cfg.http_timeout_seconds) as c:
            r = c.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return fail(
            "runner token",
            f"POST /api/runner/health failed: {type(exc).__name__}: {exc}",
        )
    if r.status_code == 401:
        return fail(
            "runner token",
            "401 unauthorized — token doesn't match any Agent",
            "rotate the token in the SaaS Runner Setup page and re-enter.",
        )
    if r.status_code != 200:
        return fail("runner token", f"unexpected status {r.status_code}")
    agent_id = "?"
    try:
        agent_id = r.json().get("agentId", "?")
    except ValueError:
        pass
    return ok(
        "runner token",
        f"accepted (last4={cfg.token_last4()}, agent={agent_id})",
    )


def check_chrome_executable() -> Result:
    path = find_chrome()
    if path is None:
        return fail(
            "Chrome installed",
            "no Chrome executable found",
            "install from https://www.google.com/chrome",
        )
    return ok("Chrome installed", str(path))


def check_chrome_cdp(cfg: RunnerConfig) -> Result:
    if is_cdp_reachable(cfg.chrome_debug_port):
        return ok(
            "Chrome CDP",
            f"reachable at {cdp_url(cfg.chrome_debug_port)}",
        )
    return warn(
        "Chrome CDP",
        f"not reachable at {cdp_version_url(cfg.chrome_debug_port)}",
        "the runner will launch Chrome on startup; this is normal if "
        "the runner isn't currently running.",
    )


def check_flow_tab(cfg: RunnerConfig) -> Result:
    """Best-effort sniff of `/json` for a Flow tab. Doesn't run when
    CDP isn't reachable — the chrome_cdp check already covered that."""
    if not is_cdp_reachable(cfg.chrome_debug_port):
        return warn("Flow tab", "skipped (CDP not reachable)")
    try:
        with httpx.Client(timeout=2.0) as c:
            r = c.get(f"{cdp_url(cfg.chrome_debug_port)}/json")
    except httpx.HTTPError as exc:
        return warn("Flow tab", f"could not list tabs: {exc}")
    if r.status_code != 200:
        return warn("Flow tab", f"/json returned {r.status_code}")
    try:
        tabs = r.json()
    except ValueError:
        return warn("Flow tab", "/json wasn't JSON")
    has_flow = any(
        ("labs.google" in (t.get("url") or "")) or
        ("flow" in (t.get("url") or "").lower())
        for t in tabs
        if isinstance(t, dict)
    )
    if has_flow:
        return ok("Flow tab", "Google Flow appears to be open")
    return warn(
        "Flow tab",
        "no labs.google / flow URL open in the dedicated profile",
        "the runner will navigate to flow on its next launch.",
    )


def check_capabilities() -> Result:
    """List known agent job types. Useful for confirming the
    bundled exe includes the modules it's supposed to."""
    try:
        # Late import — keeps `--diagnose` working even if the
        # heavy automation modules have an import-time problem.
        from src.agent_api import known_job_types
    except Exception as exc:  # noqa: BLE001
        return fail(
            "agent handlers",
            f"could not import src.agent_api: {type(exc).__name__}: {exc}",
        )
    types = known_job_types()
    if not types:
        return warn("agent handlers", "no job types registered")
    return ok("agent handlers", ", ".join(types))


def check_flow_ui_prep() -> Result:
    """Report the Flow-UI prep module's enable switches. This is a
    *dry* check — we never click anything during --diagnose, so we
    don't risk dismissing something the user actually wanted open.
    A live prep pass happens automatically at the top of every
    image / video submit; see src/flow_ui_prep.py."""
    try:
        from src import flow_ui_prep as fp
    except Exception as exc:  # noqa: BLE001
        return fail(
            "Flow UI prep",
            f"could not import src.flow_ui_prep: {type(exc).__name__}: {exc}",
        )
    enabled = fp.is_prep_enabled()
    dismiss = fp.is_dismiss_overlays_enabled()
    settings = fp.is_ensure_settings_enabled()
    debug = fp.is_debug()
    detail = (
        f"prep={'on' if enabled else 'off'} "
        f"dismiss={'on' if dismiss else 'off'} "
        f"settings={'on' if settings else 'off'} "
        f"debug={'on' if debug else 'off'}"
    )
    if not enabled:
        return warn(
            "Flow UI prep",
            detail,
            "FLOW_UI_PREP_ENABLED=false in this environment — "
            "agent prompt pills / stale menus won't be auto-dismissed.",
        )
    return ok("Flow UI prep", detail)


# ---------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------

def run_all() -> int:
    """Run every check, print the report, return 0 if no FAILs."""
    print()
    print("Flow BOF Runner — diagnostics")
    print("=============================")
    cfg_result, cfg = check_config_file()
    results: list[Result] = [
        cfg_result,
        Result("INFO", "platform", platform_label()),
        check_saas_health(cfg),
        check_runner_token(cfg),
        check_chrome_executable(),
        check_chrome_cdp(cfg),
        check_flow_tab(cfg),
        check_capabilities(),
        check_flow_ui_prep(),
    ]
    for r in results:
        print(r.render())
    print()
    any_fail = any(r.level == "FAIL" for r in results)
    any_warn = any(r.level == "WARN" for r in results)
    if any_fail:
        print("Result: one or more checks FAILED. Fix the items above + re-run.")
        return 1
    if any_warn:
        print("Result: passed with warnings. Runner should still start.")
        return 0
    print("Result: all checks passed.")
    return 0
