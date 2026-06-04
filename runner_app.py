"""Flow BOF Runner — standalone, Docker-free entrypoint.

Drives the connected-runner protocol against the hosted SaaS at
https://app.autobof.xyz (or whichever URL the user configures).

Two ways to run this:

  1. From source:
       python runner_app.py
       python runner_app.py --diagnose
       python runner_app.py --run
       python runner_app.py --setup
       python runner_app.py --reset-config
       python runner_app.py --saas-url URL --runner-token runner_xxx

  2. As a packaged Windows executable:
       FlowBOFRunner.exe

The packaged form is what end users get; the script form is what
developers use during iteration. Behaviour is identical.

What this NEVER does:
  - Require Docker, Git, Python, or a .env file.
  - Touch the user's normal Chrome profile.
  - Send Google / TikTok cookies or AI keys to the SaaS.
  - Print the runner token in full.

The original `python main.py --runner-poll` developer entrypoint is
unchanged and still works alongside this.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from dataclasses import replace

from src.runner_app import config as runner_config
from src.runner_app import diagnostics, log_setup, poller, ui_console
from src.runner_app.chrome import (
    ChromeStartupError,
    ensure_chrome_running,
    open_or_reopen_flow_browser,
)
from src.runner_app.config import (
    RunnerConfig,
    load_config,
    print_summary,
    prompt_for_missing,
    reset_config,
    save_config,
)
from src.runner_app.paths import default_chrome_profile_dir, runner_config_path


logger = logging.getLogger("runner_app")


# ---------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="FlowBOFRunner",
        description=(
            "Flow BOF Runner — connect your local machine to the "
            "hosted SaaS dashboard and run queued automation jobs."
        ),
    )
    p.add_argument(
        "--setup",
        action="store_true",
        help="Re-run the SaaS URL + runner token prompt, save config, exit.",
    )
    p.add_argument(
        "--run",
        action="store_true",
        help="Start the runner with the saved config, no menu.",
    )
    p.add_argument(
        "--diagnose",
        action="store_true",
        help="Print a readable health report for the connection + Chrome.",
    )
    p.add_argument(
        "--reset-config",
        action="store_true",
        help="Delete the saved config and exit.",
    )
    p.add_argument(
        "--open-browser",
        action="store_true",
        help=(
            "Open (or refocus) Google Flow in the dedicated runner "
            "Chrome profile and exit. Never touches your normal Chrome."
        ),
    )
    p.add_argument(
        "--inspect-flow",
        action="store_true",
        help=(
            "Attach to the runner's Chrome, capture a comprehensive DOM "
            "dump of the active Flow tab, and write JSON + text files to "
            "the runner's data directory. Use this whenever a selector "
            "starts failing — the dump shows exactly what the runner "
            "sees and is paste-friendly for bug reports."
        ),
    )
    p.add_argument(
        "--saas-url",
        default=None,
        help="Override the saved SaaS URL for this run + save.",
    )
    p.add_argument(
        "--runner-token",
        default=None,
        help="Override the saved runner token for this run + save.",
    )
    p.add_argument(
        "--no-pause",
        action="store_true",
        help="Don't pause for Enter at the end (useful in CI / piped runs).",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------
# High-level actions
# ---------------------------------------------------------------------

def _apply_overrides(cfg: RunnerConfig, args: argparse.Namespace) -> RunnerConfig:
    """Layer CLI overrides on top of the on-disk config + persist."""
    if args.saas_url:
        cfg = replace(cfg, saas_base_url=args.saas_url.strip())
    if args.runner_token:
        cfg = replace(cfg, runner_token=args.runner_token.strip())
    if args.saas_url or args.runner_token:
        save_config(cfg)
    return cfg


def do_setup() -> RunnerConfig:
    """Interactive first-run / re-setup. Returns the saved config."""
    print("Configuring Flow BOF Runner.")
    cfg = load_config()
    cfg = prompt_for_missing(cfg)
    # If the user is mid-setup we always allow overwriting the saved
    # values — this is an explicit re-prompt path.
    cfg = runner_config.replace_via_prompt_if_explicit(cfg)
    path = save_config(cfg)
    print(f"Saved config to {path}")
    print_summary(cfg)
    return cfg


def do_run(cfg: RunnerConfig) -> int:
    """Spin up Chrome + start the polling loop. Returns the
    poller's exit code (0 on clean Ctrl-C, non-zero on fatal)."""
    print_summary(cfg)
    try:
        ensure_chrome_running(
            profile_dir=runner_config.profile_dir_path(cfg),
            port=cfg.chrome_debug_port,
            open_url=cfg.flow_url,
        )
    except ChromeStartupError as exc:
        print()
        print(f"[FAIL] {exc}")
        return 2

    print()
    print("Sign in to Google Flow in the Chrome window that just opened")
    print("(if it isn't already signed in). Then leave it open while jobs run.")
    print()
    return poller.run(cfg)


def do_diagnose() -> int:
    return diagnostics.run_all()


def do_inspect_flow(cfg: RunnerConfig) -> int:
    """Run the Flow UI inspector against the runner's Chrome.

    Imports `flow_inspector` lazily so the rest of the runner CLI
    paths (--diagnose, --setup, etc.) don't pay the Playwright
    import cost just to print a help string. Returns the
    inspector's exit code so the wrapper can pause on failure.
    """
    try:
        from src.flow_inspector import run_inspector
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] Could not load flow_inspector: {exc}")
        return 5
    return run_inspector(chrome_port=cfg.chrome_debug_port)


def do_open_browser(cfg: RunnerConfig) -> int:
    """Open (or refocus) Google Flow in the dedicated runner profile.

    The hand-off into chrome.open_or_reopen_flow_browser is what
    guarantees we never poke the user's normal Chrome — it always
    operates against the configured `chromeProfileDir`. The friendly
    message describes which of the three behaviours actually fired
    (focus existing tab / open new tab / cold-launch) so the user
    knows what they should now see on screen.
    """
    try:
        result = open_or_reopen_flow_browser(
            profile_dir=runner_config.profile_dir_path(cfg),
            port=cfg.chrome_debug_port,
            flow_url=cfg.flow_url,
        )
    except ChromeStartupError as exc:
        print(f"[FAIL] {exc}")
        return 2

    status = result.get("status")
    if status == "focused":
        print(
            "Focused existing Google Flow tab in the Flow BOF Runner "
            "Chrome profile."
        )
    elif status == "opened_tab":
        print(
            "Opened Google Flow in the Flow BOF Runner Chrome profile."
        )
    elif status == "launched":
        print(
            "Launched the Flow BOF Runner Chrome profile and opened "
            "Google Flow."
        )
    else:
        print("Done.")
    return 0


def do_reset_config() -> int:
    removed = reset_config()
    if removed:
        print(f"Removed {runner_config_path()}")
    else:
        print(f"No config at {runner_config_path()} (nothing to do).")
    return 0


# ---------------------------------------------------------------------
# Interactive menu (when no CLI flag is given)
# ---------------------------------------------------------------------

def interactive(cfg: RunnerConfig) -> int:
    ui_console.title()
    if not cfg.is_ready_to_run():
        print(
            "First-time setup — we need a SaaS URL and a runner token.\n"
            "Generate the token at the SaaS Runner Setup page after signing in."
        )
        cfg = prompt_for_missing(cfg)
        save_config(cfg)
    print_summary(cfg)

    choice = ui_console.menu([
        ("run",     "Start runner"),
        ("open",    "Open/Reopen Google Flow browser"),
        ("diag",    "Run diagnostics"),
        ("inspect", "Inspect Flow UI (DOM dump for bug reports)"),
        ("setup",   "Re-enter SaaS URL / runner token"),
        ("reset",   "Reset config (deletes saved settings)"),
        ("exit",    "Exit"),
    ])
    if choice == "run":
        return do_run(cfg)
    if choice == "open":
        return do_open_browser(cfg)
    if choice == "diag":
        return do_diagnose()
    if choice == "inspect":
        return do_inspect_flow(cfg)
    if choice == "setup":
        do_setup()
        return 0
    if choice == "reset":
        return do_reset_config()
    return 0


# ---------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    log_setup.configure()
    args = parse_args(argv)

    try:
        if args.reset_config:
            return do_reset_config()
        if args.diagnose:
            cfg = load_config()
            cfg = _apply_overrides(cfg, args)
            if args.saas_url or args.runner_token:
                save_config(cfg)
            return do_diagnose()
        if args.setup:
            do_setup()
            return 0
        if args.open_browser:
            cfg = load_config()
            cfg = _apply_overrides(cfg, args)
            return do_open_browser(cfg)
        if args.inspect_flow:
            cfg = load_config()
            cfg = _apply_overrides(cfg, args)
            return do_inspect_flow(cfg)

        cfg = load_config()
        cfg = _apply_overrides(cfg, args)
        # If the user supplied overrides AND --run, jump straight in.
        if args.run or (args.saas_url and args.runner_token):
            if not cfg.is_ready_to_run():
                cfg = prompt_for_missing(cfg)
                save_config(cfg)
            return do_run(cfg)
        return interactive(cfg)
    except KeyboardInterrupt:
        # Graceful shutdown — runner_poller's signal handler has
        # already flipped its `_should_stop` flag if the loop was
        # active, so this only fires for Ctrl-C outside the loop
        # (during setup, while the menu is open, etc.).
        print("\nRunner stopped.")
        return 130
    except Exception as exc:  # noqa: BLE001
        # Last-resort safety net so the packaged exe never disappears
        # silently. The user can paste this stack trace into an issue.
        print()
        print("[FAIL] Unhandled error in the runner app:")
        print(f"  {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1


def _safe_main_with_pause() -> int:
    """Wrap main() so the console doesn't close before the user can
    read what happened. We pause on:

      - any non-zero exit (errors, Ctrl-C / SIGINT → 130, etc.)

    We skip the pause on:
      - `--no-pause` (set explicitly by CI / build smoke-tests)
      - stdin not a TTY (piped / redirected runs)

    Successful exits (menu → Exit, --diagnose with all green, etc.)
    do *not* pause — the user explicitly asked to be done.
    """
    rc = main()
    args = sys.argv[1:]
    if "--no-pause" in args:
        return rc
    if not (sys.stdin and sys.stdin.isatty()):
        return rc
    if rc == 0:
        return rc
    # Pick a message that matches the exit reason so the user
    # understands *why* we're holding the window open.
    if rc == 130:
        msg = "Runner stopped. Press Enter to exit."
    else:
        msg = "Press Enter to close."
    print()
    ui_console.pause_before_exit(msg)
    return rc


if __name__ == "__main__":
    sys.exit(_safe_main_with_pause())
