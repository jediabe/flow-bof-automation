"""Console logging — friendly format for end users.

Avoids the noisy `2026-06-03 12:34:56,789 [INFO] runner_poller:`
prefix in favour of a short timestamp + level. Errors stay obvious
because we colour them when running on a TTY that supports it.

The same logger names that the existing runner_poller / agent_api
modules use propagate up here unchanged, so all of their messages
appear without any extra wiring.
"""

from __future__ import annotations

import logging
import sys


_FMT = "%(asctime)s  %(levelname)-5s  %(message)s"
_DATEFMT = "%H:%M:%S"


def configure() -> None:
    """One-shot logging setup. Idempotent — safe to call from both
    runner_app.main and a re-entry from --diagnose."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format=_FMT,
        datefmt=_DATEFMT,
        force=True,
    )
    # httpx logs every successful request at INFO. That's useful for
    # the dev `--runner-poll` path but spammy for the packaged exe —
    # demote to WARNING so the runner's own friendly lines stay
    # visible.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
