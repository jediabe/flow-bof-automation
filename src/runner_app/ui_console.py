"""Minimal interactive menu + pause-on-exit helpers.

The packaged FlowBOFRunner.exe is double-clicked by end users; if it
crashes immediately the console window closes before they can read
the error. `pause_before_exit` keeps it open. The menu replaces the
need to type long CLI flags for the common operations.
"""

from __future__ import annotations

import sys


BANNER = r"""
=============================================================
  Flow BOF Runner
=============================================================
"""


def title() -> None:
    print(BANNER)


def pause_before_exit(message: str = "Press Enter to exit.") -> None:
    """Block until the user hits Enter. Skipped when stdin isn't a
    TTY (e.g. CI, piped runs) so automation isn't held up."""
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input(message)
    except EOFError:
        pass


def menu(choices: list[tuple[str, str]]) -> str:
    """Render a numbered menu and return the chosen key.

    `choices` is a list of (key, label) tuples. Re-prompts until the
    user enters a valid key. Returns "" on EOF (e.g. ctrl-D).
    """
    while True:
        for i, (_, label) in enumerate(choices, start=1):
            print(f"  {i}. {label}")
        try:
            raw = input("Choice: ").strip()
        except EOFError:
            return ""
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(choices):
                return choices[n - 1][0]
        # Accept the literal key too — handy when the user knows it.
        for k, _ in choices:
            if raw.lower() == k.lower():
                return k
        print(f"(invalid choice: {raw})")
