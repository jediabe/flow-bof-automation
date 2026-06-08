"""Playwright driver for Google Flow Labs.

Two browser modes are supported:

  remote_debugging   — attach to a real Chrome instance the user launched
                       with --remote-debugging-port. Required for Google
                       sign-in (Google blocks Playwright's bundled Chromium).
                       We connect over CDP, reuse the existing context and
                       page, and never close the user's Chrome.

  persistent_profile — fallback. Launch Playwright's bundled Chromium with
                       a persistent user-data-dir. Useful for sites that
                       don't fingerprint the browser.

Selectors live in config.py — do not embed them here.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .config import (
    BROWSER_MODE_PERSISTENT_PROFILE,
    BROWSER_MODE_REMOTE_DEBUGGING,
    Settings,
)
from .flow_tiles import TileInfo
from .recorded_flow import (
    RecordedFlowError,
    enter_new_project_if_present,
    perform_recorded_flow,
)
from .utils import Product


class FlowAutomationError(RuntimeError):
    pass


@dataclass
class FlowSession:
    """Resolved browser handle. Use this instead of raw BrowserContext.

    When `is_attached` is True we are bound to a real Chrome the user owns:
    do NOT close `context` or `browser`. New pages we open during work are
    closed individually.
    """

    context: BrowserContext
    is_attached: bool
    browser: Browser | None = None  # only set in remote_debugging mode


@contextmanager
def open_flow_browser(settings: Settings, logger: logging.Logger) -> Iterator[FlowSession]:
    """Yield a FlowSession appropriate for the configured BROWSER_MODE."""
    with sync_playwright() as pw:
        if settings.browser_mode == BROWSER_MODE_REMOTE_DEBUGGING:
            session = _attach_remote_chrome(pw, settings, logger)
            try:
                yield session
            finally:
                # Disconnect from CDP without closing the user's Chrome.
                try:
                    if session.browser is not None:
                        session.browser.close()
                except Exception:  # noqa: BLE001
                    logger.exception("Error disconnecting from remote Chrome")
        elif settings.browser_mode == BROWSER_MODE_PERSISTENT_PROFILE:
            session = _launch_persistent_profile(pw, settings)
            try:
                yield session
            finally:
                try:
                    session.context.close()
                except Exception:  # noqa: BLE001
                    logger.exception("Error closing persistent context")
        else:
            raise FlowAutomationError(f"Unknown BROWSER_MODE: {settings.browser_mode!r}")


def _attach_remote_chrome(
    pw: Playwright, settings: Settings, logger: logging.Logger
) -> FlowSession:
    logger.info("Connecting to Chrome over CDP at %s", settings.chrome_cdp_url)
    try:
        browser = pw.chromium.connect_over_cdp(settings.chrome_cdp_url)
    except Exception as exc:  # noqa: BLE001
        raise FlowAutomationError(
            f"Could not connect to Chrome at {settings.chrome_cdp_url}. "
            f"Is Chrome running with --remote-debugging-port=9222? "
            f"See README → Windows remote debugging setup. ({exc})"
        ) from exc

    if browser.contexts:
        context = browser.contexts[0]
    else:
        # Headless real-Chrome edge case: no default context. Create one.
        context = browser.new_context()

    # Anti-fingerprint: suppress navigator.webdriver via CDP rather
    # than via the Chrome launch flag --disable-blink-features=
    # AutomationControlled. The launch-flag approach works but
    # triggers Chrome's yellow "unsupported command-line flag"
    # infobar, which is itself a visible signal that the browser is
    # automated. Doing the patch inside the page via init script
    # achieves the same fingerprint suppression with no banner.
    #
    # Two-step apply:
    #   1. context.add_init_script — runs before every future page
    #      load in this context.
    #   2. Evaluate on already-open pages — the Flow tab the user
    #      has open at connection time pre-dates the init script
    #      registration, so we have to patch it directly.
    try:
        context.add_init_script(_STEALTH_INIT_JS)
    except Exception as exc:  # noqa: BLE001
        # add_init_script can't fail at the JS level (it's just a
        # CDP registration), but be defensive — never crash the
        # connect over a stealth patch.
        logger.warning("stealth init-script registration failed: %s", exc)
    for existing_page in context.pages:
        try:
            existing_page.evaluate(_STEALTH_INIT_JS)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "stealth patch on existing page failed (continuing): %s", exc,
            )

    logger.info(
        "Attached to Chrome %s — %d existing context(s), %d page(s); "
        "stealth init script registered",
        browser.version,
        len(browser.contexts),
        len(context.pages),
    )
    return FlowSession(context=context, is_attached=True, browser=browser)


# JavaScript that runs in every page (via context.add_init_script for
# future loads, and via page.evaluate for already-open tabs) to
# suppress the most basic automation fingerprints Google's risk
# engine looks at. Kept minimal — patching too much creates its own
# fingerprint (the patches themselves can be detected via property
# descriptors, getter source code, etc.).
#
# Approach: only patch when navigator.webdriver is TRUE (i.e. Chrome
# was launched with --enable-automation or similar). Set it to FALSE
# (not undefined!) because false is what a real user's Chrome
# reports. Setting to undefined is detectable as "someone patched
# this" because real Chrome never has undefined here.
#
# In our typical setup — user launches Chrome via the
# start_chrome_debug script which only sets --remote-debugging-port
# — webdriver is ALREADY false. The patch is then a no-op. We keep
# the code in place defensively for users who might have Chrome
# already running with --enable-automation from some other tool.
#
# What this does NOT patch (deliberately):
#   - navigator.plugins / .languages: already populated correctly on
#     a real Chrome with a real user profile.
#   - chrome.runtime: present on a real Chrome.
#   - User-Agent: real, not spoofed.
#   - WebGL / Canvas: real GPU, real driver.
# These were the big differences when the launcher used Playwright's
# own bundled Chromium. We don't have that problem because we connect
# to the user's actual installed Chrome.
_STEALTH_INIT_JS = r"""
(() => {
  // Conditional patch: only override webdriver when it's set to
  // true. The natural value on a real user's Chrome is false; if
  // it's already false (typical for our setup since we don't pass
  // --enable-automation when launching Chrome), don't touch it —
  // overriding false with anything makes things MORE detectable,
  // not less.
  try {
    if (navigator.webdriver === true) {
      Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
        configurable: true,
      });
    }
  } catch (_e) { /* swallow — better to fail open than crash */ }
})();
"""


def _launch_persistent_profile(pw: Playwright, settings: Settings) -> FlowSession:
    settings.browser_user_data_dir.mkdir(parents=True, exist_ok=True)
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(settings.browser_user_data_dir),
        headless=settings.headless,
        slow_mo=settings.slow_mo_ms,
        viewport={"width": 1280, "height": 900},
        accept_downloads=True,
    )
    return FlowSession(context=context, is_attached=False)


# ---------------------------------------------------------------------------
# Page acquisition
# ---------------------------------------------------------------------------


def _is_flow_url(url: str, settings: Settings) -> bool:
    """Loose match: same host as FLOW_LABS_URL."""
    if not url:
        return False
    try:
        target = urlparse(settings.flow_labs_url).netloc.lower()
        actual = urlparse(url).netloc.lower()
        return bool(target) and actual == target
    except Exception:  # noqa: BLE001
        return False


def acquire_flow_page(
    session: FlowSession, settings: Settings, logger: logging.Logger
) -> Page:
    """Find an existing Flow Labs tab, or open one. Does not log in.

    Returns a Page ready for automation. If we attached to real Chrome and
    a Flow Labs tab is already open, we reuse it (no navigation) — the user
    may be in the middle of a project.
    """
    if session.is_attached:
        for page in session.context.pages:
            try:
                if _is_flow_url(page.url, settings):
                    logger.info("Reusing existing Flow Labs tab: %s", page.url)
                    return page
            except Exception:  # noqa: BLE001
                continue
        # No Flow tab open — open one and navigate.
        page = session.context.new_page()
        logger.info("No Flow Labs tab found. Opening %s", settings.flow_labs_url)
        page.goto(settings.flow_labs_url, wait_until="domcontentloaded")
        return page

    # persistent_profile mode: always make a fresh page and navigate.
    page = session.context.new_page()
    logger.info("Navigating to %s", settings.flow_labs_url)
    page.goto(settings.flow_labs_url, wait_until="domcontentloaded")
    return page


# ---------------------------------------------------------------------------
# Setup / health-check commands
# ---------------------------------------------------------------------------


REMOTE_DEBUGGING_INSTRUCTIONS = r"""
You are in BROWSER_MODE=remote_debugging.

1. Close all running Chrome windows (or use a separate Chrome profile).
2. Launch Chrome from PowerShell or cmd:

   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\chrome-flow-automation"

3. In that Chrome window, sign in to your Google account and open Flow Labs:
   https://labs.google/flow

4. Leave Chrome open. Come back here and press Enter.
"""


def run_setup_browser(settings: Settings, logger: logging.Logger) -> int:
    """Walk the user through one-time browser setup for the current mode."""
    if settings.browser_mode == BROWSER_MODE_REMOTE_DEBUGGING:
        return _setup_remote_debugging(settings, logger)
    return _setup_persistent_profile(settings, logger)


def _setup_remote_debugging(settings: Settings, logger: logging.Logger) -> int:
    print(REMOTE_DEBUGGING_INSTRUCTIONS)
    print(f"Expecting CDP at: {settings.chrome_cdp_url}")
    print(f"Expecting Flow Labs at: {settings.flow_labs_url}\n")
    try:
        input("Press Enter once Chrome is running and you are logged into Flow Labs...")
    except EOFError:
        pass

    # Test the connection without holding onto the browser.
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(settings.chrome_cdp_url)
            try:
                summary = _summarize_browser(browser, settings)
                _print_browser_summary(summary)
                if not summary.has_flow_tab:
                    print(
                        "\nNo Flow Labs tab detected. Open "
                        f"{settings.flow_labs_url} in that Chrome window, "
                        "then run `python main.py --check-browser` to verify."
                    )
                else:
                    print("\nFlow Labs tab detected. You're ready to run.")
            finally:
                browser.close()  # disconnect, does not kill Chrome
    except Exception as exc:  # noqa: BLE001
        logger.error("CDP connection failed: %s", exc)
        print(
            f"\nFAILED to connect to {settings.chrome_cdp_url}. "
            "Double-check that Chrome is running with --remote-debugging-port=9222."
        )
        return 1
    return 0


def _setup_persistent_profile(settings: Settings, logger: logging.Logger) -> int:
    logger.info(
        "BROWSER_MODE=persistent_profile. Opening Playwright's Chromium for manual login."
    )
    with open_flow_browser(settings, logger) as session:
        page = session.context.new_page()
        page.goto(settings.flow_labs_url, wait_until="domcontentloaded")
        print(
            "Browser open. Log in with your Google account, then press Enter here.\n"
            "Note: Google may block sign-in in Playwright's Chromium. "
            "If it does, switch BROWSER_MODE=remote_debugging in .env."
        )
        try:
            input("Press Enter after logging into Flow Labs...")
        except EOFError:
            page.wait_for_timeout(60_000)
    return 0


@dataclass
class BrowserSummary:
    version: str
    pages: list[tuple[str, str]]  # (url, title)
    has_flow_tab: bool


def _summarize_browser(browser: Browser, settings: Settings) -> BrowserSummary:
    pages_info: list[tuple[str, str]] = []
    has_flow = False
    for context in browser.contexts:
        for page in context.pages:
            try:
                url = page.url
            except Exception:  # noqa: BLE001
                url = "<unknown>"
            try:
                title = page.title()
            except Exception:  # noqa: BLE001
                title = "<unavailable>"
            pages_info.append((url, title))
            if _is_flow_url(url, settings):
                has_flow = True
    return BrowserSummary(version=browser.version, pages=pages_info, has_flow_tab=has_flow)


def _print_browser_summary(summary: BrowserSummary) -> None:
    print(f"Connected. Chrome version: {summary.version}")
    print(f"Open pages ({len(summary.pages)}):")
    for url, title in summary.pages:
        print(f"  - {title!r}\n      {url}")


def run_check_browser(settings: Settings, logger: logging.Logger) -> int:
    """Verify CDP connectivity and report on the live Chrome session."""
    if settings.browser_mode != BROWSER_MODE_REMOTE_DEBUGGING:
        print(
            f"BROWSER_MODE={settings.browser_mode}. --check-browser is only meaningful "
            f"in remote_debugging mode."
        )
        return 0

    logger.info("Checking CDP at %s", settings.chrome_cdp_url)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(settings.chrome_cdp_url)
            try:
                summary = _summarize_browser(browser, settings)
                _print_browser_summary(summary)
                if summary.has_flow_tab:
                    print(f"\nFlow Labs reachable: yes ({settings.flow_labs_url})")
                    return 0
                print(
                    f"\nFlow Labs tab not found. Open {settings.flow_labs_url} "
                    f"in the Chrome window, log in, and re-run --check-browser."
                )
                return 1
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("CDP connection failed: %s", exc)
        print(
            f"FAILED to connect to {settings.chrome_cdp_url}.\n"
            "Launch Chrome with:\n"
            '  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            '--remote-debugging-port=9222 '
            '--user-data-dir="%USERPROFILE%\\chrome-flow-automation"'
        )
        return 1


# ---------------------------------------------------------------------------
# Per-product automation
# ---------------------------------------------------------------------------


def _screenshot_on_error(page: Page, settings: Settings, slug: str, logger: logging.Logger) -> None:
    try:
        path = settings.logs_dir / f"error_{datetime.now():%Y%m%d_%H%M%S}_{slug}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.error("Saved error screenshot: %s", path)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to capture error screenshot")


def generate_image_for_product(
    session: FlowSession,
    settings: Settings,
    product: Product,
    prompt: str,
    output_image: Path,
    logger: logging.Logger,
) -> list[TileInfo]:
    """Run a single Flow Labs generation.

    Returns the list of TileInfos captured after the click (one per
    variant — Flow typically emits 2 unless variants are pinned to 1x).
    Empty list if capture timed out. The orchestration layer persists
    every captured media_id on the CSV row so `--sync-favorites` can
    match by membership regardless of which variant the user hearts.
    """
    page = acquire_flow_page(session, settings, logger)
    page.set_default_timeout(settings.selector_timeout_ms)

    try:
        # If we are on the Flow Labs landing page (e.g. first run of the
        # day), enter a new project and apply the per-project settings.
        # When already inside a project, this is a no-op.
        enter_new_project_if_present(
            page,
            logger=logger,
            selector_timeout_ms=settings.selector_timeout_ms,
        )

        try:
            tiles = perform_recorded_flow(
                page,
                str(product.product_image_path),
                prompt,
                logger=logger,
                selector_timeout_ms=settings.selector_timeout_ms,
                generation_timeout_seconds=settings.generation_timeout_seconds,
                verify_generation_started=settings.verify_generation_started,
                wait_for_result=settings.save_output_image,
                capture_timeout_seconds=settings.capture_timeout_seconds,
                capture_sibling_window_ms=settings.image_sibling_window_ms,
                fast_submit_mode=settings.image_fast_submit_mode,
                debug_screenshots=settings.debug_screenshots,
            )
        except RecordedFlowError as exc:
            _screenshot_on_error(page, settings, product.slug, logger)
            raise FlowAutomationError(str(exc)) from exc

        if settings.save_output_image and tiles:
            first_with_src = next((t for t in tiles if t.flow_image_src), None)
            if first_with_src:
                absolute_url = _make_absolute_url(first_with_src.flow_image_src, page.url)
                _save_image_url(session.context, absolute_url, output_image)
                logger.info("Saved %s", output_image)
        elif not settings.save_output_image:
            logger.info("Skipping image save (SAVE_OUTPUT_IMAGE=false)")

        return tiles

    except Exception:
        _screenshot_on_error(page, settings, product.slug, logger)
        raise
    finally:
        if not session.is_attached:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass


def _make_absolute_url(src: str, page_url: str) -> str:
    """Resolve a possibly-relative result src against the current page URL.

    Flow Labs serves result images from a relative path like
    `/fx/api/trpc/media.getMediaUrlRedirect?name=...`. urljoin handles
    leading-slash and same-document references correctly. data: and
    blob: URLs are returned unchanged.
    """
    if src.startswith(("data:", "blob:")):
        return src
    return urljoin(page_url, src)


def _save_image_url(context: BrowserContext, url: str, output_image: Path) -> None:
    """Download the image at `url` (which must already be absolute or data:)."""
    if url.startswith("data:"):
        import base64

        header, _, payload = url.partition(",")
        data = base64.b64decode(payload) if ";base64" in header else payload.encode("utf-8")
        output_image.write_bytes(data)
        return

    if url.startswith("blob:"):
        raise FlowAutomationError(
            "Result image is a blob: URL — needs to be read via page.evaluate. "
            "Update recorded_flow.py to return an HTTP URL instead."
        )

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FlowAutomationError(f"Unsupported image URL scheme: {url[:64]}")

    response = context.request.get(url)
    if not response.ok:
        raise FlowAutomationError(f"Failed to download image: HTTP {response.status}")
    output_image.write_bytes(response.body())
