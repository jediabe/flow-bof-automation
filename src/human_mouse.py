"""Real-OS mouse synthesis for the human-click hot path.

The default Playwright/Patchright mouse path goes through CDP
`Input.dispatchMouseEvent`. The events DO carry `isTrusted=true`
(confirmed via chromium-dev mailing list), but the *path and
cadence* fingerprint above the trust bit is detectable: trained
classifiers identify naive Bezier-curve synthetic mouse trajectories
with ~96-98% accuracy (arxiv 2410.18233). reCAPTCHA Enterprise's
behavioral scorer is one such system.

This module replaces CDP mouse events with pyautogui's OS-level
synthesis (Windows: SendInput, macOS: CGEventPost, Linux: XTest)
for the brief click moment. The browser still sees genuine OS
mouse events from the host. We use the deep-research-validated
overshoot+correction pattern (Ghost Cursor style) and randomise
the landing point inside the element's bounding box rather than
clicking the centre.

When pyautogui isn't usable (not installed, headless, no display,
window not focused, DPI conversion fails), the caller falls back
to the CDP path. That keeps the runner resilient on systems where
OS-level synthesis would crash.

Environment knobs:
    MOUSE_STRATEGY=cdp        # force CDP (legacy behaviour)
    MOUSE_STRATEGY=os_native  # default — try pyautogui, fall back
    MOUSE_OVERSHOOT_PX=18     # max overshoot px on long moves
    MOUSE_OVERSHOOT_MIN_DIST=320  # only overshoot moves longer than this
    MOUSE_MOVE_DURATION_RANGE=0.25,0.85  # min,max seconds per move
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Optional


# Cached lazy import. pyautogui is heavy and pulls X11/Quartz/Win32
# stubs at import time; we don't want it loaded unless the user
# actually opts into MOUSE_STRATEGY=os_native.
_pyautogui = None
_pyautogui_load_attempted = False
_pyautogui_load_error: str | None = None


def _load_pyautogui():
    global _pyautogui, _pyautogui_load_attempted, _pyautogui_load_error
    if _pyautogui_load_attempted:
        return _pyautogui
    _pyautogui_load_attempted = True
    try:
        import pyautogui  # noqa: PLC0415
        # Disable pyautogui's built-in fail-safe (corner-of-screen
        # abort) — would surprise an end user automating their own
        # machine if they happened to move their real mouse to the
        # top-left corner mid-batch.
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0  # we control timing ourselves
        _pyautogui = pyautogui
    except Exception as exc:  # noqa: BLE001
        _pyautogui_load_error = f"{type(exc).__name__}: {exc}"
        _pyautogui = None
    return _pyautogui


def strategy() -> str:
    """Return the resolved mouse strategy: 'cdp' or 'os_native'.

    Reads MOUSE_STRATEGY at call time so env changes take effect
    on the next click without a runner restart.

    v0.6.15-alpha shipped this defaulted to 'os_native' but an
    end-user on Windows reported clicks landing off-target across
    a whole batch (UI selectors timing out because the upstream
    "+" upload click missed the button → reference image never
    attached → "Add to Prompt" element never rendered). Almost
    certainly Windows DPI scaling: pyautogui isn't DPI-aware by
    default, so at 125%/150% display scale every click lands at
    `coord * scale` instead of `coord`. Until we ship proper DPI
    detection, default to the known-safe CDP path. Operators
    who've verified pyautogui works on their machine can opt in
    with MOUSE_STRATEGY=os_native.
    """
    raw = (os.getenv("MOUSE_STRATEGY") or "cdp").strip().lower()
    return raw if raw in ("cdp", "os_native") else "cdp"


@dataclass
class ViewportToScreen:
    """Offset to convert page-relative (CSS) coords → screen coords.

    Captured via JS each click because the browser window can be
    moved between clicks. Cheap (one page.evaluate per click).
    """
    screen_x: float       # window.screenX
    screen_y: float       # window.screenY + chrome height
    chrome_height: float  # outerHeight - innerHeight
    device_pixel_ratio: float

    def to_screen(self, page_x: float, page_y: float) -> tuple[float, float]:
        """Convert a page-relative CSS pixel coord to screen px.

        On macOS Retina, pyautogui uses LOGICAL pixels (1:1 with
        CSS), so we don't multiply by DPR. On Windows with PER_MONITOR
        DPI awareness, pyautogui also uses logical pixels.
        Conclusion: skip the DPR multiply; CSS px == pyautogui px
        on supported platforms.
        """
        return (
            self.screen_x + page_x,
            self.screen_y + page_y,
        )


_VIEWPORT_JS = r"""
() => {
  return {
    screenX: window.screenX,
    screenY: window.screenY,
    outerHeight: window.outerHeight,
    innerHeight: window.innerHeight,
    outerWidth: window.outerWidth,
    innerWidth: window.innerWidth,
    devicePixelRatio: window.devicePixelRatio || 1,
    documentHasFocus: document.hasFocus(),
  };
}
"""


def get_viewport_offset(page) -> Optional[ViewportToScreen]:
    """Read window.screenX/Y + chrome height via JS. Returns None
    on any failure — caller falls back to CDP."""
    try:
        info = page.evaluate(_VIEWPORT_JS)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(info, dict):
        return None
    try:
        outer_h = float(info.get("outerHeight") or 0)
        inner_h = float(info.get("innerHeight") or 0)
        chrome_h = max(0.0, outer_h - inner_h)
        return ViewportToScreen(
            screen_x=float(info.get("screenX") or 0),
            screen_y=float(info.get("screenY") or 0) + chrome_h,
            chrome_height=chrome_h,
            device_pixel_ratio=float(info.get("devicePixelRatio") or 1),
        )
    except (TypeError, ValueError):
        return None


def _move_duration_range() -> tuple[float, float]:
    raw = os.getenv("MOUSE_MOVE_DURATION_RANGE", "0.25,0.85")
    try:
        a, b = (float(s.strip()) for s in raw.split(","))
        if a < 0.05 or b > 5.0 or a > b:
            return (0.25, 0.85)
        return (a, b)
    except Exception:  # noqa: BLE001
        return (0.25, 0.85)


def _overshoot_settings() -> tuple[float, float]:
    """(max_overshoot_px, min_distance_to_trigger_overshoot)."""
    try:
        m = float(os.getenv("MOUSE_OVERSHOOT_PX", "18"))
        d = float(os.getenv("MOUSE_OVERSHOOT_MIN_DIST", "320"))
        return (max(0.0, m), max(0.0, d))
    except Exception:  # noqa: BLE001
        return (18.0, 320.0)


def os_native_click(
    page,
    target_x_page: float,
    target_y_page: float,
    *,
    log: logging.Logger | None = None,
) -> bool:
    """Move + click via real OS-level mouse synthesis.

    Returns True on success, False to signal the caller should fall
    back to CDP. Reasons for False:
      - pyautogui not importable on this system
      - viewport JS failed (page navigated mid-click, etc.)
      - document.hasFocus() == False (Chrome window isn't focused;
        OS clicks would hit whatever IS focused)
      - DPI conversion produced suspicious values (negative coords,
        coords outside the screen)

    Never raises — fallback is the responsibility of the caller, but
    a panic here would just abort the click which is worse than CDP.
    """
    log = log or logging.getLogger("flow_bof")

    if strategy() != "os_native":
        return False

    pg = _load_pyautogui()
    if pg is None:
        log.debug(
            "os_native_click: pyautogui unavailable (%s); falling back to CDP",
            _pyautogui_load_error,
        )
        return False

    offset = get_viewport_offset(page)
    if offset is None:
        log.debug("os_native_click: viewport JS failed; falling back to CDP")
        return False

    # If the window isn't focused, OS-level clicks hit whatever IS
    # focused — disastrous. Skip os-native in that case; CDP works
    # fine on a backgrounded window.
    try:
        has_focus = bool(page.evaluate("() => document.hasFocus()"))
    except Exception:  # noqa: BLE001
        has_focus = False
    if not has_focus:
        log.debug("os_native_click: document.hasFocus()==False; falling back to CDP")
        return False

    screen_x, screen_y = offset.to_screen(target_x_page, target_y_page)
    # Sanity: pyautogui will silently clip / wrap weird coords. Refuse
    # negative or absurdly-large values rather than risk firing a
    # click somewhere the user can't see.
    try:
        screen_w, screen_h = pg.size()
    except Exception:  # noqa: BLE001
        return False
    if not (0 <= screen_x <= screen_w and 0 <= screen_y <= screen_h):
        log.debug(
            "os_native_click: target (%s, %s) outside screen %sx%s; falling back to CDP",
            screen_x, screen_y, screen_w, screen_h,
        )
        return False

    # Distance from current cursor → target. Drives whether we
    # overshoot.
    try:
        cur_x, cur_y = pg.position()
    except Exception:  # noqa: BLE001
        cur_x, cur_y = (screen_x, screen_y)
    dist = ((screen_x - cur_x) ** 2 + (screen_y - cur_y) ** 2) ** 0.5

    max_overshoot, min_overshoot_dist = _overshoot_settings()
    move_min, move_max = _move_duration_range()

    try:
        if dist >= min_overshoot_dist and max_overshoot > 0:
            # Overshoot-and-correct: aim PAST the target on the line
            # from current → target, then correct back. Ghost-Cursor
            # style. Fitts's-law-ish: longer moves overshoot more.
            overshoot_amt = random.uniform(max_overshoot * 0.4, max_overshoot)
            scale = (dist + overshoot_amt) / max(dist, 1.0)
            past_x = cur_x + (screen_x - cur_x) * scale
            past_y = cur_y + (screen_y - cur_y) * scale
            pg.moveTo(
                past_x, past_y,
                duration=random.uniform(move_min, move_max),
                tween=pg.easeOutQuad,
            )
            # Brief pause between overshoot and correction — humans
            # don't reverse on the same frame.
            time.sleep(random.uniform(0.04, 0.13))
            pg.moveTo(
                screen_x, screen_y,
                duration=random.uniform(0.08, 0.22),
                tween=pg.easeInOutQuad,
            )
        else:
            # Short move — no overshoot, but still use an easing
            # tween so the path isn't a perfect straight line.
            pg.moveTo(
                screen_x, screen_y,
                duration=random.uniform(move_min, move_max),
                tween=pg.easeInOutQuad,
            )

        # Pre-click dwell — humans typically settle 50-200ms on the
        # target before clicking. This is on top of the move tween.
        time.sleep(random.uniform(0.05, 0.20))

        # Click! Randomise the mousedown→mouseup gap (the "click
        # duration") so it's not a uniform 0ms which is itself a
        # tell.
        pg.mouseDown(button="left")
        time.sleep(random.uniform(0.04, 0.14))
        pg.mouseUp(button="left")
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("os_native_click: pyautogui raised (%s); falling back to CDP", exc)
        return False
