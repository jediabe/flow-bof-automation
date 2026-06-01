"""Setup / health-check helpers for the Streamlit Setup page.

Each ``check_*`` function returns ``(status, message)`` where status is
one of ``"ok"`` / ``"warn"`` / ``"fail"`` and message is short and safe
to display.

The functions are intentionally cheap (HEAD/GET with short timeouts,
no Flow automation) so the user can re-run them as often as they want.
"""

from __future__ import annotations

import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from .batch_workflow import list_batches
from .config import load_settings

Status = Literal["ok", "warn", "fail"]


def _http_head(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """HEAD a URL with a short timeout. Falls back to GET for servers
    that don't speak HEAD. Returns (ok, message)."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status < 400), f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # 405 Method Not Allowed = server is alive but doesn't like HEAD.
        if exc.code in (405, 501):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    return (resp.status < 400), f"HTTP {resp.status}"
            except Exception as inner:
                return False, f"{type(inner).__name__}: {str(inner)[:80]}"
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


def check_docker_app_running() -> tuple[Status, str]:
    # If the Streamlit UI is rendering, the ui container is up. The CLI
    # ``app`` service is short-lived (run --rm). We can verify the
    # docker daemon is reachable from this container by checking
    # /var/run/docker.sock — but the ui container deliberately doesn't
    # mount that socket. Best we can do here is confirm we are inside a
    # container, which means *some* part of the stack is up.
    in_container = os.path.exists("/.dockerenv")
    return (
        ("ok", "UI container is running.")
        if in_container
        else ("warn", "Not detected as running inside a container.")
    )


def check_chrome_debug_reachable() -> tuple[Status, str]:
    settings = load_settings()
    base = settings.chrome_cdp_url.rstrip("/")
    ok, msg = _http_head(f"{base}/json/version", timeout=3.0)
    if ok:
        return "ok", f"Chrome debugger reachable at {base}."
    return "fail", f"Chrome debugger unreachable at {base} ({msg})."


def check_flow_labs_reachable() -> tuple[Status, str]:
    settings = load_settings()
    ok, msg = _http_head(settings.flow_labs_url, timeout=4.0)
    if ok:
        return "ok", f"Flow Labs reachable ({msg})."
    return "warn", (
        f"Flow Labs URL {settings.flow_labs_url} not reachable from the UI "
        f"container ({msg}). This is informational — the actual login happens "
        f"in your Chrome window, not from this container."
    )


def check_ai_provider_configured() -> tuple[Status, str]:
    from ai.prompt_generator import get_provider

    name = (os.environ.get("AI_PROVIDER") or "manual").strip().lower()
    try:
        provider = get_provider(name)
    except ValueError as exc:
        return "fail", str(exc)
    ok, msg = provider.is_configured()
    if name == "manual":
        return "warn", "AI provider is 'manual' — you'll write prompts by hand."
    if ok:
        return "ok", f"{name}: {msg}"
    return "fail", f"{name}: {msg}"


def check_folders_writable() -> tuple[Status, str]:
    settings = load_settings()
    targets: list[Path] = [
        settings.repo_root / "data" / "batches",
        settings.inputs_dir,
        settings.reference_images_dir,
        settings.logs_dir,
    ]
    failures: list[str] = []
    for path in targets:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".health_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        return "fail", "; ".join(failures)[:200]
    return "ok", f"{len(targets)} folders writable."


def check_current_batch() -> tuple[Status, str]:
    batches = list_batches()
    if not batches:
        return "warn", "No batches yet — create one from the BOF page."
    return "ok", f"{len(batches)} batch(es): {', '.join(batches[:3])}{'...' if len(batches) > 3 else ''}"


def run_all_checks() -> list[dict]:
    """Return the full setup checklist as a list of dicts."""
    return [
        {"label": "Docker UI container",     "status": s, "message": m}
        for s, m in [check_docker_app_running()]
    ] + [
        {"label": "Chrome remote debugging", "status": s, "message": m}
        for s, m in [check_chrome_debug_reachable()]
    ] + [
        {"label": "Flow Labs reachable",     "status": s, "message": m}
        for s, m in [check_flow_labs_reachable()]
    ] + [
        {"label": "AI provider",             "status": s, "message": m}
        for s, m in [check_ai_provider_configured()]
    ] + [
        {"label": "Folders writable",        "status": s, "message": m}
        for s, m in [check_folders_writable()]
    ] + [
        {"label": "Batch exists",            "status": s, "message": m}
        for s, m in [check_current_batch()]
    ]
