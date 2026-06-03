"""Locate, launch, and probe a dedicated debug Chrome instance.

The standalone runner connects to Chrome via CDP at
http://127.0.0.1:<port>. The user's normal Chrome (if any) is left
alone — we always use a separate `--user-data-dir` so cookies, signed-
in accounts, and extensions don't bleed across the boundary.

Three responsibilities:
  - Find a Chrome executable to launch.
  - Probe whether a debug Chrome is already running on the chosen port.
  - Spawn a fresh one with the right flags + open the Flow URL.

The launched Chrome is intentionally **not** held in this process —
we Popen it and detach. The runner keeps polling the SaaS regardless
of whether the user closes the Chrome window; the next /check-browser
or job will surface the disconnect cleanly.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable


logger = logging.getLogger("runner_app.chrome")


# ---------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------

def candidate_chrome_paths() -> list[Path]:
    """Per-platform list of paths to try, most-likely first."""
    home = Path.home()
    if sys.platform.startswith("win"):
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get(
            "LOCALAPPDATA", str(home / "AppData" / "Local")
        )
        return [
            Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    if sys.platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        ]
    # Linux / other Unix.
    return [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/snap/bin/chromium"),
    ]


def find_chrome() -> Path | None:
    """Return the first existing path from candidate_chrome_paths().
    None when nothing's installed — the caller renders a friendly
    'install Chrome' message."""
    for c in candidate_chrome_paths():
        if c.exists() and c.is_file():
            return c
    return None


# ---------------------------------------------------------------------
# CDP probe
# ---------------------------------------------------------------------

def cdp_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def cdp_version_url(port: int) -> str:
    return f"{cdp_url(port)}/json/version"


def is_cdp_reachable(port: int, timeout: float = 1.5) -> bool:
    """True iff `GET /json/version` returns a 200 with a usable
    `webSocketDebuggerUrl`. False on any error — the caller decides
    whether to launch a fresh Chrome."""
    try:
        with urllib.request.urlopen(cdp_version_url(port), timeout=timeout) as r:
            if r.status != 200:
                return False
            body = r.read().decode("utf-8", errors="replace")
            return "webSocketDebuggerUrl" in body
    except (urllib.error.URLError, ValueError, OSError):
        return False


# ---------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------

def launch_chrome(
    *,
    chrome_path: Path,
    profile_dir: Path,
    port: int,
    open_url: str | None = None,
) -> subprocess.Popen[bytes]:
    """Spawn Chrome with the required debug flags. Returns the Popen
    handle so the caller can `terminate()` on shutdown if it wants
    to — most users won't.

    Flags we set, all required:

      --remote-debugging-port=<port>
          The CDP endpoint Playwright connects to.
      --remote-debugging-address=127.0.0.1
          Bind loopback only. (The Docker setup needed 0.0.0.0 so
          host.docker.internal could reach in; the standalone runner
          shares the host so loopback is correct and safer.)
      --remote-allow-origins=*
          Chrome 116+ blocks WebSocket handshakes without this.
      --user-data-dir=<path>
          Dedicated FlowBOF profile. Keeps the user's normal Chrome
          untouched.
      --no-first-run --no-default-browser-check
          Skip the "set as default" / welcome flow.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    args: list[str] = [
        str(chrome_path),
        f"--remote-debugging-port={int(port)}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={str(profile_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if open_url:
        args.append(open_url)

    logger.info("launching Chrome: %s", chrome_path)
    logger.info("  --remote-debugging-port=%d", int(port))
    logger.info("  --user-data-dir=%s", profile_dir)
    if open_url:
        logger.info("  open %s", open_url)

    # Windows: DETACHED_PROCESS + new console group so the child
    # survives the runner closing its console window. POSIX: leave as
    # default; the user can Ctrl-C the runner without killing Chrome
    # because Chrome puts itself in its own process group.
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin":  subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(args, **kwargs)  # noqa: S603


def wait_until_cdp_reachable(port: int, deadline_seconds: float = 30.0) -> bool:
    """Spin-wait until /json/version answers, or `deadline_seconds`
    elapse. ~250ms polling, ~30s default budget — enough for cold-
    start on a slow disk without making the runner feel hung."""
    end = time.monotonic() + max(1.0, deadline_seconds)
    while time.monotonic() < end:
        if is_cdp_reachable(port):
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------
# Top-level entrypoint used by runner_app.poller
# ---------------------------------------------------------------------

class ChromeStartupError(RuntimeError):
    """Raised when we can't bring debug Chrome up. The message is
    user-readable; the caller can print it verbatim."""


def ensure_chrome_running(
    *,
    profile_dir: Path,
    port: int,
    open_url: str | None,
) -> dict:
    """Make sure a debug Chrome is reachable on `port`. Reuse an
    already-running instance when possible, otherwise launch a fresh
    one against `profile_dir` and (optionally) open `open_url`.

    Returns a small status dict (`reused` / `launched`, executable
    path, etc.) the caller can log. Never returns silently on
    failure — raises ChromeStartupError with a human message.
    """
    if is_cdp_reachable(port):
        logger.info("debug Chrome already reachable at %s", cdp_url(port))
        return {"status": "reused", "port": port}

    chrome_path = find_chrome()
    if chrome_path is None:
        raise ChromeStartupError(
            "Google Chrome is required. Please install Chrome from "
            "https://www.google.com/chrome and run the runner again."
        )

    launch_chrome(
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        port=port,
        open_url=open_url,
    )

    if not wait_until_cdp_reachable(port, deadline_seconds=30.0):
        raise ChromeStartupError(
            f"Chrome started but the debug port {port} did not become "
            f"reachable within 30 seconds. Close any existing Chrome "
            f"windows that aren't using --user-data-dir="
            f"{profile_dir} and try again."
        )

    logger.info("debug Chrome ready at %s", cdp_url(port))
    return {
        "status": "launched",
        "port": port,
        "chrome_path": str(chrome_path),
        "profile_dir": str(profile_dir),
    }


def platform_label() -> str:
    """One-line platform fingerprint for diagnostics."""
    return (
        f"{platform.python_implementation()} {platform.python_version()} "
        f"on {platform.system()} {platform.release()}"
    )


# ---------------------------------------------------------------------
# Reopen Flow in the dedicated profile (NOT in the user's normal Chrome)
# ---------------------------------------------------------------------

def _list_tabs(port: int) -> list[dict]:
    """`GET /json` on the CDP endpoint. Returns the list of open
    targets (tabs / service workers / etc). Empty list on any error
    — caller treats that as "no info, fall through."""
    try:
        with urllib.request.urlopen(
            f"{cdp_url(port)}/json", timeout=2.0,
        ) as r:
            if r.status != 200:
                return []
            import json as _json  # local — keeps the module top tight
            data = _json.loads(r.read().decode("utf-8", errors="replace"))
            return data if isinstance(data, list) else []
    except (urllib.error.URLError, ValueError, OSError):
        return []


def _looks_like_flow_tab(tab: dict) -> bool:
    """Heuristic: any 'page' target whose URL hits labs.google or
    contains /flow. Strict enough to skip the new-tab page; loose
    enough to survive Google shuffling the Flow URL inside
    labs.google.*."""
    if not isinstance(tab, dict):
        return False
    if (tab.get("type") or "") != "page":
        return False
    url = (tab.get("url") or "").lower()
    return ("labs.google" in url) or ("/flow" in url)


def _cdp_request(method: str, path: str, port: int, timeout: float = 5.0) -> bool:
    """One-shot HTTP request at the CDP endpoint. Used for
    `PUT /json/new` (open new tab) and `PUT /json/activate/<id>`
    (focus an existing tab). Returns True on 2xx, False otherwise."""
    req = urllib.request.Request(
        f"{cdp_url(port)}{path}", method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError):
        return False


def open_or_reopen_flow_browser(
    *,
    profile_dir: Path,
    port: int,
    flow_url: str,
) -> dict:
    """Open or refocus Google Flow inside the dedicated runner
    Chrome profile.

    The runner uses a separate `--user-data-dir`, so this function
    NEVER touches the user's normal Chrome windows or their default
    profile. Three behaviours, top-down:

      1. CDP reachable AND a Flow-shaped tab already exists →
         activate (focus) that tab. No new Chrome process.
      2. CDP reachable AND no Flow tab found → open a new tab in
         the existing dedicated Chrome via `PUT /json/new`. No new
         Chrome process.
      3. CDP NOT reachable → launch a fresh debug Chrome against
         `profile_dir` and load `flow_url`. Same code path as
         the runner's initial startup.

    Explicitly does NOT call any global Stop-Process-style kill,
    never iterates "every Chrome on the machine", and never
    operates on a chrome whose `--user-data-dir` isn't this
    runner's. Normal browsing stays untouched.

    Returns a small status dict like `{"status": "focused"}` /
    `{"status": "opened_tab"}` / `{"status": "launched"}` so the
    caller can render a friendly message.
    """
    if is_cdp_reachable(port):
        # CDP up — we own a debug Chrome on this port already.
        tabs = _list_tabs(port)
        for t in tabs:
            if _looks_like_flow_tab(t):
                target_id = t.get("id") or t.get("targetId")
                if target_id and _cdp_request(
                    "PUT", f"/json/activate/{target_id}", port,
                ):
                    logger.info(
                        "focused existing Flow tab in dedicated profile",
                    )
                    return {"status": "focused", "target_id": target_id}
                # activate failed (older Chromium that only accepts
                # GET, or a wedged tab). Fall through to open a new
                # tab rather than spawning a second Chrome.
                break

        # No usable Flow tab. PUT /json/new is the Chrome 92+
        # endpoint; some forks still only accept GET.
        encoded = urllib.parse.quote(flow_url, safe=":/?&=#")
        if _cdp_request("PUT", f"/json/new?{encoded}", port):
            logger.info("opened new Flow tab in dedicated profile (PUT)")
            return {"status": "opened_tab", "url": flow_url}
        if _cdp_request("GET", f"/json/new?{encoded}", port):
            logger.info("opened new Flow tab in dedicated profile (GET)")
            return {"status": "opened_tab", "url": flow_url}

        # CDP endpoint locked down for /json/new. Last resort:
        # invoke Chrome with the *same* user-data-dir + the URL.
        # Chrome's single-instance-per-profile dispatcher routes
        # the URL into the existing window as a new tab. This
        # never starts a second Chrome process.
        chrome_path = find_chrome()
        if chrome_path is not None:
            subprocess.Popen(  # noqa: S603
                [
                    str(chrome_path),
                    f"--user-data-dir={str(profile_dir)}",
                    flow_url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            logger.info(
                "opened new Flow tab in dedicated profile (chrome cli)",
            )
            return {
                "status": "opened_tab",
                "url": flow_url,
                "via": "chrome-cli",
            }

        raise ChromeStartupError(
            "CDP is reachable but /json/new was rejected and Chrome "
            "is no longer installed at a known location. Reinstall "
            "Google Chrome and try again."
        )

    # CDP not reachable → spin up the dedicated Chrome from scratch.
    # ensure_chrome_running raises ChromeStartupError on a friendly
    # error if Chrome is missing.
    ensure_chrome_running(
        profile_dir=profile_dir,
        port=port,
        open_url=flow_url,
    )
    return {"status": "launched", "url": flow_url}
