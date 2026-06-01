"""Open Playwright Inspector against the live Flow Labs Chrome session.

Usage:
    python scripts/record_flow_actions.py

Assumes BROWSER_MODE=remote_debugging and that Chrome is already running
with --remote-debugging-port=9222 and you are signed into Flow Labs.

What happens:
    1. We attach to your live Chrome over CDP.
    2. We reuse (or open) a Flow Labs tab in that Chrome window.
    3. We call page.pause(), which opens the Playwright Inspector window.
    4. In the Inspector you click "Record". Each click/type you perform
       on Flow Labs is emitted as a Python locator line.
    5. Copy those lines into the matching `_locate_*` helpers in
       src/recorded_flow.py. Then run `python main.py --run-one`.

If Inspector recording is not available in this Playwright build,
Inspector still works as a "Pick locator" tool — click the picker icon
and hover any Flow Labs element to get its suggested locator.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.config import BROWSER_MODE_REMOTE_DEBUGGING, load_settings  # noqa: E402


def _find_or_open_flow_page(context, flow_url: str):
    for page in context.pages:
        try:
            if "labs.google" in page.url:
                return page
        except Exception:  # noqa: BLE001
            continue
    page = context.new_page()
    page.goto(flow_url, wait_until="domcontentloaded")
    return page


def main() -> int:
    settings = load_settings()
    if settings.browser_mode != BROWSER_MODE_REMOTE_DEBUGGING:
        print(
            "BROWSER_MODE is not remote_debugging. Recording works best when "
            "you can sign in normally — set BROWSER_MODE=remote_debugging "
            "in .env and launch Chrome with --remote-debugging-port=9222."
        )

    # PWDEBUG=1 makes Playwright pause on every action and shows the
    # Inspector. We only need page.pause() once, but setting this helps
    # the Inspector open reliably across Playwright versions.
    os.environ.setdefault("PWDEBUG", "1")

    with sync_playwright() as pw:
        print(f"Connecting to Chrome at {settings.chrome_cdp_url} ...")
        browser = pw.chromium.connect_over_cdp(settings.chrome_cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = _find_or_open_flow_page(context, settings.flow_labs_url)
            page.bring_to_front()

            print()
            print("Playwright Inspector is opening.")
            print("In the Inspector window:")
            print('  1. Click "Record".')
            print("  2. In your Chrome window, click through the full Flow Labs")
            print("     flow once: upload an image, click +, click Add to Prompt,")
            print("     type a prompt, click the generate arrow.")
            print("  3. The Inspector emits one Python line per action.")
            print("  4. Copy each line into the matching _locate_* helper in")
            print("     src/recorded_flow.py.")
            print("  5. When done, click Resume in the Inspector to release.")
            print()

            page.pause()
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
