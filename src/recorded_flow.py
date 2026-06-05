"""Executes the recorded Flow Labs UI flow.

This file is the seam between the manual `playwright codegen` recording
and the rest of the automation. Paste locators from the Inspector into
the `_locate_*` helpers below — each has a short note describing what
the recorded action looks like.

Why this file exists separately from `flow_automation.py`:
    Selectors guessed without seeing the live DOM are brittle. Recording
    a single successful session gives the exact locator strings
    Playwright considers stable for this build of Flow Labs. Keeping all
    such locators in one file makes re-recording trivial when the UI
    changes — diff this file, not the orchestration logic.

Every "I did X" log line below is gated on a state check, so the log
can be trusted to reflect reality.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Iterable

from playwright.sync_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    expect,
)

from .flow_tiles import TileInfo, scan_tiles


_REPO_ROOT = Path(__file__).resolve().parent.parent
_AT_PROMPT_SCREENSHOT = _REPO_ROOT / "outputs" / "logs" / "before_add_to_prompt_click.png"


class RecordedFlowError(RuntimeError):
    """Raised when a recorded step or its post-condition check fails."""


# ---------------------------------------------------------------------------
# Locator hooks — paste the recorded codegen output below.
#
# After running `python scripts/record_flow_actions.py` and clicking
# through Flow Labs in the Inspector, Playwright emits Python lines like
#     page.get_by_role("button", name="...").click()
#     page.get_by_placeholder("...").fill("...")
# Replace each function body with the matching locator expression. Keep
# the signatures stable — the orchestration in `perform_recorded_flow`
# depends on them.
# ---------------------------------------------------------------------------


def _locate_new_project_button(page: Page) -> Locator:
    # Recorded at (892, 799) with text "add_2\nNew project". "New project"
    # is unique to this button.
    return page.locator("button").filter(has_text="New project").first


def _locate_project_settings_trigger(page: Page) -> Locator:
    """Composer-toolbar pill that summarises the active model,
    aspect ratio, and variant count. Clicking it opens the
    settings popover with Image/Video tabs.

    The recorded selector matched on the "crop_" Material icon
    ligature (the aspect glyph used to render as "crop_9_16",
    "crop_16_9", etc.). Flow re-skinned the pill on 2026-06-04:
    aspect is now a phone-shape icon, no "crop_" text. The new
    invariant the pill always carries is **the active model name**
    — match on that first, with the legacy ligature as a fallback
    so older builds still work.

    Excludes buttons with `role="tab"` so we never match the
    variant tabs that appear inside the popover after it opens.
    """
    log = logging.getLogger("flow_bof")
    model_re = re.compile(
        r"(nano\s+banana|veo\s+\d|gemini|imagen|omni\s+flash|wan|seedance)",
        re.I,
    )
    strategies: list[tuple[str, "callable[[], Locator]"]] = [
        ("button containing a model name (nano-banana / veo / etc.)",
         lambda: page.locator("button:not([role='tab'])").filter(
             has_text=model_re,
         )),
        ("button:has-text('crop_')  [legacy ligature]",
         lambda: page.locator("button:not([role='tab'])").filter(
             has_text="crop_",
         )),
    ]
    for label, build in strategies:
        try:
            locator = build().first
            locator.wait_for(state="visible", timeout=2_000)
            log.info("Settings trigger resolved via: %s", label)
            return locator
        except (PlaywrightTimeoutError, AssertionError):
            continue
    log.warning(
        "Settings trigger: no strategy resolved — returning legacy "
        "locator so caller's existing TimeoutError handling fires.",
    )
    return page.locator("button").filter(has_text="crop_").first


def _locate_aspect_9_16_tab(page: Page) -> Locator:
    # Recorded as role="tab" at (1190, 720) with text "crop_9_16\n9:16".
    return page.get_by_role("tab").filter(has_text="9:16").first


def _locate_variants_1x_tab(page: Page) -> Locator:
    # Recorded as role="tab" at (979, 777) with text "1x".
    return page.get_by_role("tab", name="1x").first


_IMAGE_MODE_TAB_PICKER_JS = r"""
() => {
  // The settings popover has a top-level "Image" / "Video" mode
  // selector. Empirically Flow ships this as one of:
  //   - <button role="tab" aria-selected="..."> with text 'Image'
  //   - <button role="radio" aria-checked="..."> with text 'Image'
  //   - Plain <button> with a Material icon ligature ('image')
  //     prefix + the visible label 'Image'
  // The recorded selector (role=tab name=/^image$/) misses the
  // icon-prefix and the radio shapes. This picker walks every open
  // popover-ish container, looks for any clickable whose innerText
  // (after stripping a leading 'image' / 'movie' icon ligature)
  // equals 'Image' or 'Image generation', tags it with a data
  // attribute, and returns its details for the runner log.
  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
           cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const cleanText = txt => {
    // Strip a leading Material icon ligature so 'image\nImage' or
    // 'movie\nVideo' collapses to just the label.
    return (txt || '')
      .replace(/^\s*(image|movie|crop_\w+|add\w*|arrow_\w+|swap_\w+)\s*/i, '')
      .trim();
  };

  const containers = Array.from(document.querySelectorAll(
    '[role="dialog"], [data-radix-popper-content-wrapper], ' +
    '[data-state="open"], [role="tablist"], [role="radiogroup"]'
  )).filter(isVisible);
  // Also include the body itself as a last-resort scope — some
  // builds render the mode toggle inline next to the composer
  // instead of inside a popover.
  containers.push(document.body);

  for (const c of containers) {
    const candidates = Array.from(c.querySelectorAll(
      '[role="tab"], [role="radio"], button'
    )).filter(isVisible);
    for (const el of candidates) {
      const raw = (el.innerText || el.textContent || '').trim();
      const lbl = cleanText(raw).toLowerCase();
      if (lbl !== 'image' && lbl !== 'image generation') continue;
      // Skip the obvious false positives: the bottom-of-page
      // "Image" library link, anything that ALSO mentions Video
      // (e.g. a combined toggle that's not the tab).
      if (/video/i.test(raw) && raw.length > 40) continue;
      document.querySelectorAll('[data-flow-bof-image-tab]').forEach(
        e => e.removeAttribute('data-flow-bof-image-tab')
      );
      el.setAttribute('data-flow-bof-image-tab', '1');
      const r = el.getBoundingClientRect();
      return {
        ok: true,
        text: raw.slice(0, 60),
        role: el.getAttribute('role'),
        aria_selected: el.getAttribute('aria-selected'),
        aria_checked: el.getAttribute('aria-checked'),
        aria_pressed: el.getAttribute('aria-pressed'),
        rect: {
          x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height),
        },
      };
    }
  }
  return {error: 'no Image-mode toggle found in any open container'};
}
"""


def _locate_image_mode_tab(page: Page) -> Locator:
    """Multi-strategy lookup for Flow's Image/Video mode toggle in
    the settings popover.

    The composer remembers its last mode. When the previous run
    was video, the popover lands on Video and the image-mode
    controls (Nano Banana Pro picker, + button on the composer)
    are absent from the DOM. The runner MUST be able to flip the
    composer back to image mode or every downstream image step
    fails silently.

    Empirically the toggle ships as either a `role="tab"`, a
    `role="radio"` (Radix RadioGroup), or a plain `<button>` with
    a Material icon ligature `image` prefix. Try each shape; on
    every miss fall through to a JS-driven picker that scans
    every open popover/tablist for a clickable whose label
    (after stripping an icon-ligature prefix) reads "Image".

    The JS picker tags the chosen element with
    `data-flow-bof-image-tab="1"` and the returned locator binds
    to that attribute, so the click goes to exactly the element
    the picker chose. Marker is stripped on every call so a stale
    tag from a previous run can't accidentally bind.
    """
    log = logging.getLogger("flow_bof")
    name_re = re.compile(r"^\s*image\s*$", re.I)

    # Strategy 1 — JS picker (most reliable; handles icon-prefix +
    # role variants in one pass).
    try:
        result = page.evaluate(_IMAGE_MODE_TAB_PICKER_JS)
        if isinstance(result, dict) and result.get("ok"):
            log.info(
                "Image tab JS picker selected: text=%r role=%r aria-selected=%s rect=%s",
                result.get("text"),
                result.get("role"),
                result.get("aria_selected"),
                result.get("rect"),
            )
            return page.locator('[data-flow-bof-image-tab="1"]').first
        elif isinstance(result, dict) and result.get("error"):
            log.info(
                "Image tab JS picker: %s", result.get("error"),
            )
    except Exception as exc:  # noqa: BLE001
        log.info("Image tab JS picker errored: %s", _short_err(exc))

    # Strategies 2-N — semantic role fallbacks.
    strategies: list[tuple[str, "callable[[], Locator]"]] = [
        ("get_by_role(tab, name~Image)",
         lambda: page.get_by_role("tab", name=name_re)),
        ("get_by_role(radio, name~Image)",
         lambda: page.get_by_role("radio", name=name_re)),
        ("get_by_role(button, name~Image)",
         lambda: page.get_by_role("button", name=name_re)),
    ]
    for label, build in strategies:
        try:
            locator = build().first
            locator.wait_for(state="visible", timeout=1_500)
            log.info("Image tab resolved via: %s", label)
            return locator
        except (PlaywrightTimeoutError, AssertionError):
            continue

    log.warning(
        "Image tab: every strategy missed. Returning a never-resolving "
        "locator so the caller's try/except sees a clean timeout."
    )
    return page.locator(
        '[data-flow-bof-image-tab="never-set"]'
    ).first


def _composer_is_in_image_mode(page: Page) -> bool | None:
    """Cheap visual check: is the composer currently rendering
    image-mode controls?

    Returns True when the composer's settings-pill text starts with
    "Image" (or — fallback — a + button is present and the
    "swap_horiz" video-mode button isn't). False when the pill
    text starts with "Video" or the swap button is present. None
    when we can't tell (composer hidden, page not loaded yet).

    Cheap: one page.evaluate, no waits. Designed to be called as
    a verification step AFTER a mode switch click, so we know
    whether the click actually flipped the DOM.
    """
    try:
        return page.evaluate(r"""
() => {
  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
           cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const textbox = document.querySelector(
    'div[role="textbox"][contenteditable="true"]'
  );
  if (!textbox || !isVisible(textbox)) return null;
  let scope = textbox;
  for (let i = 0; i < 6 && scope.parentElement; i++) {
    scope = scope.parentElement;
  }
  const buttons = Array.from(scope.querySelectorAll('button'))
    .filter(isVisible);
  // Settings-pill check: the pill text starts with "Image" or
  // "Video". This is the most reliable signal once Flow has
  // rendered the composer fully.
  for (const b of buttons) {
    const t = (b.innerText || b.textContent || '').trim();
    if (/^Image\b/i.test(t)) return true;
    if (/^Video\b/i.test(t)) return false;
  }
  // Fallback: presence of the swap_horiz button is video-only,
  // presence of an 'add'-ligature button is image-only.
  const hasSwap = buttons.some(b =>
    /swap_horiz|swap first/i.test((b.innerText || b.textContent || ''))
  );
  if (hasSwap) return false;
  const hasAdd = buttons.some(b =>
    /^add(_\d)?\b/i.test((b.innerText || b.textContent || '').trim())
  );
  if (hasAdd) return true;
  return null;
}
""")
    except Exception:  # noqa: BLE001
        return None


def _pin_image_model(
    page: Page,
    *,
    logger: logging.Logger,
    target_name: str = "Nano Banana Pro",
    selector_timeout_ms: int = 15_000,
) -> bool:
    """Ensure the image-model dropdown is set to `target_name`.

    Why this exists: the recorded selector clicked a `<span>` whose
    text was the target model name, which only works when that
    model is ALREADY selected (its name shows in the dropdown
    trigger). When the user has manually switched to a different
    image model (Nano Banana 2, Imagen, etc.), the target option
    isn't in the DOM until the dropdown is opened first.

    Mirrors `ensure_veo_lite_model` from flow_ui_prep: find
    trigger → read current → if mismatched, click open → click
    target → verify. Best-effort throughout — every failure logs
    a warning and returns False; the caller continues with
    whatever model is currently selected.

    Returns True iff the target model is the active selection
    after this call (either because it already was, or because
    we successfully switched).
    """
    name_re = re.compile(
        r"\b" + re.escape(target_name) + r"\b", re.I,
    )
    known_image_models_re = re.compile(
        r"(nano\s+banana|imagen|gemini)", re.I,
    )

    # ---- 1. Find the model trigger inside the open popover ----
    trigger: Locator | None = None
    trigger_strategies = [
        ("popover-scoped button with image-model name",
         lambda: page.locator(
             "[role='dialog'], [data-radix-popper-content-wrapper], "
             "[data-state='open']"
         ).locator("button").filter(
             has_text=known_image_models_re,
         ).first),
        ("get_by_role(combobox) with image-model name",
         lambda: page.get_by_role("combobox").filter(
             has_text=known_image_models_re,
         ).first),
        ("any button with image-model name (page-wide)",
         lambda: page.locator("button").filter(
             has_text=known_image_models_re,
         ).first),
    ]
    for label, build in trigger_strategies:
        try:
            cand = build()
            if cand.is_visible(timeout=500):
                trigger = cand
                logger.info("Image model trigger via: %s", label)
                break
        except (PlaywrightTimeoutError, AssertionError):
            continue
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "  trigger strategy %r errored: %s",
                label, _short_err(exc),
            )

    if trigger is None:
        logger.warning(
            "Image model trigger not found in settings popover; "
            "leaving model unchanged (was probably %r-class).",
            target_name,
        )
        return False

    # ---- 2. Read current selection ----
    current = ""
    try:
        current = (trigger.inner_text(timeout=500) or "").strip()
    except Exception:  # noqa: BLE001
        pass
    if name_re.search(current):
        logger.info(
            "Image model already %r — no change",
            current.replace("\n", " ⏎ ")[:60],
        )
        return True

    # ---- 3. Open the dropdown ----
    try:
        trigger.click(timeout=selector_timeout_ms)
        page.wait_for_timeout(300)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not open image model dropdown (current=%r): %s",
            current.replace("\n", " ⏎ ")[:60], _short_err(exc),
        )
        return False

    # ---- 4. Find the target option inside the open dropdown ----
    option: Locator | None = None
    option_strategies = [
        ("get_by_role(option, name~target)",
         lambda: page.get_by_role("option", name=name_re).first),
        ("get_by_role(menuitem, name~target)",
         lambda: page.get_by_role("menuitem", name=name_re).first),
        ("get_by_role(menuitemradio, name~target)",
         lambda: page.get_by_role("menuitemradio", name=name_re).first),
        ("listbox/menu-scoped text match",
         lambda: page.locator(
             "[role='listbox'], [role='menu'], "
             "[data-radix-popper-content-wrapper]"
         ).locator(
             f":text-matches('{re.escape(target_name)}', 'i')"
         ).first),
        ("any visible :text-matches target",
         lambda: page.locator(
             f":text-matches('{re.escape(target_name)}', 'i')"
         ).first),
    ]
    for label, build in option_strategies:
        try:
            cand = build()
            if cand.is_visible(timeout=1_500):
                option = cand
                logger.info("Image model option via: %s", label)
                break
        except (PlaywrightTimeoutError, AssertionError):
            continue
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "  option strategy %r errored: %s",
                label, _short_err(exc),
            )

    if option is None:
        logger.warning(
            "%r option not visible in image model dropdown after "
            "opening; current model %r stays.",
            target_name, current.replace("\n", " ⏎ ")[:60],
        )
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        return False

    # ---- 5. Click + verify ----
    try:
        option.click(timeout=selector_timeout_ms)
        page.wait_for_timeout(300)
        logger.info(
            "Switched image model: %r → %s",
            current.replace("\n", " ⏎ ")[:60] or "(unknown)",
            target_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Clicking %r option failed: %s",
            target_name, _short_err(exc),
        )
        return False

    # Verify by re-reading the trigger text. If the dropdown
    # closed and the trigger now shows the target, we're good.
    try:
        new_text = (trigger.inner_text(timeout=500) or "").strip()
        if name_re.search(new_text):
            return True
        logger.warning(
            "Image model click registered but trigger text is %r "
            "— click may not have taken.",
            new_text.replace("\n", " ⏎ ")[:60],
        )
    except Exception:  # noqa: BLE001
        # If we can't re-read the trigger, assume the click took.
        pass
    return True


def _locate_model_nano_banana_pro(page: Page) -> Locator:
    """Multi-strategy lookup for the Nano Banana Pro model option.

    The recording captured this as a <span>, but Flow has since
    rewrapped the picker — depending on build it surfaces as a
    Radix combobox button, a <button> containing the model name,
    a role="option" inside an open dropdown, or the legacy span.
    Try each shape in priority order; the first visible match wins.

    Never raises directly — when nothing resolves, returns the
    legacy span locator so the caller's existing click() will fail
    with a familiar PlaywrightTimeoutError that the warning path
    in _apply_project_settings already handles.
    """
    log = logging.getLogger("flow_bof")
    name_re = re.compile(r"Nano\s+Banana\s+Pro", re.I)
    strategies: list[tuple[str, "callable[[], Locator]"]] = [
        ("get_by_role(option, name~Nano Banana Pro)",
         lambda: page.get_by_role("option", name=name_re)),
        ("get_by_role(menuitem, name~Nano Banana Pro)",
         lambda: page.get_by_role("menuitem", name=name_re)),
        ("get_by_role(button, name~Nano Banana Pro)",
         lambda: page.get_by_role("button", name=name_re)),
        ("button:has-text(/Nano Banana Pro/i)",
         lambda: page.locator("button").filter(has_text=name_re)),
        ("span:has-text(/Nano Banana Pro/i)  [legacy]",
         lambda: page.locator("span").filter(has_text=name_re)),
    ]
    for label, build in strategies:
        try:
            locator = build().first
            locator.wait_for(state="visible", timeout=2_000)
            log.info("Nano Banana Pro resolved via: %s", label)
            return locator
        except (PlaywrightTimeoutError, AssertionError):
            continue
    log.warning(
        "Nano Banana Pro: no strategy resolved a visible match — "
        "falling back to legacy span locator (click will likely "
        "timeout). Are you on the Image tab in the settings popover?"
    )
    return page.locator("span").filter(has_text=name_re).first


def _locate_file_input(page: Page) -> Locator:
    # NOT in action_recording_20260528_065723.json — the recording had no
    # file_change event. The standard hidden <input type="file"> is still
    # the most reliable target; re-record an upload to confirm.
    return page.locator('input[type="file"]').first


_PLUS_BUTTON_PICKER_JS = r"""
() => {
  // The composer "+" / attachment button is always the LEFTMOST
  // button on the same visual row as the textbox, after excluding
  // the Agent pill, the settings pill (crop_*), the send arrow
  // (arrow_forward), variant tabs (1x/2x/3x/4x), and any button
  // that contains a model name. We can't rely on icon ligatures
  // (they change between Flow builds) or on DOM order alone
  // (`preceding::button[1]` returns the Agent pill, which sits
  // immediately before the textbox in document order).
  const textbox = document.querySelector(
    'div[role="textbox"][contenteditable="true"]'
  );
  if (!textbox) return {error: 'no contenteditable textbox'};
  let scope = textbox;
  for (let i = 0; i < 6 && scope.parentElement; i++) {
    scope = scope.parentElement;
  }
  const tbRect = textbox.getBoundingClientRect();

  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
           cs.visibility !== 'hidden' && cs.display !== 'none';
  };

  const isSameRow = el => {
    const r = el.getBoundingClientRect();
    // Same visual row as the textbox (allows a generous band so
    // we still pick the + even when the composer is multiline).
    return !(r.bottom < tbRect.top - 20 || r.top > tbRect.bottom + 80);
  };

  const isRejected = el => {
    const text = ((el.innerText || el.textContent || '')
                  .trim().toLowerCase());
    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
    if (text === 'agent' || aria === 'agent') return 'agent';
    if (aria.includes('send') || text.includes('arrow_forward')) return 'send';
    if (text.includes('crop_') || aria.includes('settings')) return 'settings_pill';
    if (text.includes('nano banana') || text.includes('veo')) return 'model_name';
    if (/^\s*[1-9]x\s*$/.test(text)) return 'variant_tab';
    if (text.includes('add to prompt')) return 'add_to_prompt';
    return null;
  };

  const buttons = Array.from(scope.querySelectorAll('button'))
    .filter(isVisible);
  const sameRow = buttons.filter(isSameRow);
  const filtered = [];
  const rejected = [];
  for (const b of sameRow) {
    const reason = isRejected(b);
    if (reason) {
      rejected.push({
        text: (b.innerText || b.textContent || '').slice(0, 30).trim(),
        aria: b.getAttribute('aria-label'),
        reason,
      });
    } else {
      filtered.push(b);
    }
  }

  if (filtered.length === 0) {
    return {
      error: 'no candidates after reject filter',
      scanned: buttons.length,
      same_row: sameRow.length,
      rejected,
    };
  }
  filtered.sort((a, b) =>
    a.getBoundingClientRect().left - b.getBoundingClientRect().left
  );
  const winner = filtered[0];
  // Strip any stale marker from a previous call so the locator
  // can't accidentally bind to the wrong button.
  scope.querySelectorAll('[data-flow-bof-plus]').forEach(
    e => e.removeAttribute('data-flow-bof-plus')
  );
  winner.setAttribute('data-flow-bof-plus', '1');
  const wr = winner.getBoundingClientRect();
  return {
    ok: true,
    text: (winner.innerText || winner.textContent || '').slice(0, 60).trim(),
    aria_label: winner.getAttribute('aria-label'),
    role: winner.getAttribute('role'),
    rect: {
      x: Math.round(wr.x), y: Math.round(wr.y),
      w: Math.round(wr.width), h: Math.round(wr.height),
    },
    class_name: (winner.className || '').toString().slice(0, 80),
    candidates_considered: filtered.length,
    rejected_count: rejected.length,
  };
}
"""


def _locate_plus_button_via_js(page: Page) -> Locator:
    """Pick the composer + button by visual position, not by text
    or DOM-order. Tags the chosen element with
    `data-flow-bof-plus="1"` and returns a locator bound to that
    attribute. Robust to Material icon renames and to additional
    composer-toolbar buttons being added next to the textbox.

    Raises PlaywrightTimeoutError when no candidate matches so
    the existing strategy-loop falls through to the diagnostic
    dump.
    """
    log = logging.getLogger("flow_bof")
    result = page.evaluate(_PLUS_BUTTON_PICKER_JS)
    if not isinstance(result, dict):
        log.info("plus-button JS picker: unexpected result type %s", type(result))
        raise PlaywrightTimeoutError("JS picker returned non-dict")
    if result.get("error"):
        log.info(
            "plus-button JS picker: %s (scanned=%s same_row=%s rejected=%s)",
            result.get("error"),
            result.get("scanned"),
            result.get("same_row"),
            result.get("rejected"),
        )
        raise PlaywrightTimeoutError(f"JS picker: {result['error']}")
    log.info(
        "plus-button JS picker selected: text=%r aria=%r rect=%s (of %d candidate(s); rejected %d)",
        result.get("text"),
        result.get("aria_label"),
        result.get("rect"),
        result.get("candidates_considered"),
        result.get("rejected_count"),
    )
    return page.locator('[data-flow-bof-plus="1"]').first


_COMPOSER_BUTTON_DUMP_JS = r"""
() => {
  const textbox = document.querySelector('div[role="textbox"][contenteditable="true"]');
  if (!textbox) return {error: "no contenteditable textbox found"};
  // Walk up a few levels so the dump scope covers the whole
  // composer toolbar (the textbox itself is just one row).
  let scope = textbox;
  for (let i = 0; i < 6 && scope.parentElement; i++) {
    scope = scope.parentElement;
  }
  const buttons = Array.from(scope.querySelectorAll('button')).slice(0, 16);
  return buttons.map(b => {
    const rect = b.getBoundingClientRect();
    const cs = window.getComputedStyle(b);
    return {
      text: (b.innerText || b.textContent || '').slice(0, 60).trim(),
      aria_label: b.getAttribute('aria-label'),
      role: b.getAttribute('role'),
      visible:
        rect.width > 0 && rect.height > 0 &&
        cs.visibility !== 'hidden' && cs.display !== 'none',
      rect: {
        x: Math.round(rect.x), y: Math.round(rect.y),
        w: Math.round(rect.width), h: Math.round(rect.height),
      },
      class_name: (b.className || '').toString().slice(0, 80),
    };
  });
}
"""


def _dump_composer_buttons(page: Page, log: logging.Logger) -> None:
    """Log every button in the composer toolbar for triage.

    Called when `_locate_plus_button` exhausts every strategy. The
    output goes straight into the runner log so we don't need a
    devtools session to figure out which button selector to add.
    """
    try:
        result = page.evaluate(_COMPOSER_BUTTON_DUMP_JS)
    except Exception as exc:  # noqa: BLE001
        log.warning("composer-button dump failed: %s", exc)
        return
    if isinstance(result, dict) and result.get("error"):
        log.warning("composer-button dump: %s", result["error"])
        return
    log.info("Composer-area buttons (%d):", len(result))
    for b in result:
        log.info(
            "  <button aria=%s role=%s vis=%s rect=%s class=%s> %r",
            b.get("aria_label") or "-",
            b.get("role") or "-",
            b.get("visible"),
            b.get("rect"),
            b.get("class_name") or "-",
            b.get("text"),
        )


def _locate_plus_button(page: Page) -> Locator:
    """Robust composer "+" / attachment button lookup.

    The recording matched on the Material Symbols ligature "add_2"
    rendered as literal text inside the button. Subsequent Flow
    builds have renamed that ligature (commonly to "add", sometimes
    swapped for an inline SVG with no text at all). Try a sequence
    of selectors anchored on aria-label and structural position,
    then fall back to ligature-name variants. When every strategy
    misses we dump the composer-area button list so the next debug
    cycle has real DOM data instead of guesses.
    """
    log = logging.getLogger("flow_bof")
    strategies: list[tuple[str, "callable[[], Locator]"]] = [
        # 1) Visual-position picker. The composer + is always the
        # leftmost button on the textbox's row that ISN'T the Agent
        # pill / settings pill / send arrow / variant tab / model
        # name. This handles icon-only buttons with no aria-label
        # and survives Material ligature renames. Runs first because
        # it's the most reliable; everything else is a fallback for
        # builds where the textbox query fails.
        (
            "JS picker (leftmost composer-row non-target)",
            lambda: _locate_plus_button_via_js(page),
        ),
        # 2) aria-label is the next most stable surface — Flow's
        # accessible labels survive icon-name churn. Exclude the
        # "Add to Prompt" button which lives in the upload popover
        # (matches the same 'add' substring).
        (
            "button[aria-label*='Add' i] not 'Prompt'",
            lambda: page.locator(
                "button[aria-label*='add' i]:not([aria-label*='prompt' i])"
            ),
        ),
        (
            "button[aria-label*='Attach' i]",
            lambda: page.locator("button[aria-label*='attach' i]"),
        ),
        (
            "button[aria-label*='Insert' i]",
            lambda: page.locator("button[aria-label*='insert' i]"),
        ),
        # 3) Ligature fallbacks — current text could be any common
        # Material icon name for a "+" glyph.
        (
            "button:has-text /^(add_2|add|add_circle|add_box|plus|attach_file)$/",
            lambda: page.locator("button").filter(
                has_text=re.compile(
                    r"^\s*(add_2|add|add_circle|add_box|plus|attach_file)\s*$",
                    re.I,
                )
            ),
        ),
    ]

    last_error: Exception | None = None
    for label, build in strategies:
        try:
            locator = build().first
            locator.wait_for(state="visible", timeout=3_000)
            log.info("Composer plus resolved via: %s", label)
            return locator
        except (PlaywrightTimeoutError, AssertionError) as exc:
            last_error = exc
            log.info("  strategy missed: %s (%s)", label, _short_err(exc))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.info("  strategy errored: %s (%s)", label, _short_err(exc))

    _dump_composer_buttons(page, log)
    raise RecordedFlowError(
        f"Composer + button not found by any strategy. Last error: {last_error}"
    )


def _locate_add_to_prompt(page: Page) -> Locator:
    """Robust "Add to Prompt" lookup.

    Flow Labs does not expose this element with a stable role/structure
    — depending on the build, it shows up as a button, a menu item, a
    plain <div>, or a clickable span. We try a sequence of strategies
    and fall back to picking the smallest visible element whose text is
    "Add to Prompt".

    Before the lookup we dump a diagnostic screenshot and log every
    candidate's tag/text/role/aria-label/bbox so a failed run is
    trivial to triage.
    """
    log = logging.getLogger("flow_bof")
    _diagnostics_before_add_to_prompt(page, log)

    strategies: list[tuple[str, "callable[[], Locator]"]] = [
        (
            "get_by_role(button, name~Add to Prompt)",
            lambda: page.get_by_role("button", name=re.compile("Add to Prompt", re.I)),
        ),
        (
            "get_by_text(/^Add to Prompt$/i)",
            lambda: page.get_by_text(re.compile(r"^Add to Prompt$", re.I)),
        ),
        (
            "button:has-text('Add to Prompt')",
            lambda: page.locator("button:has-text('Add to Prompt')"),
        ),
        (
            "div:has-text('Add to Prompt') minus Upload-containing div",
            lambda: page.locator("div:has-text('Add to Prompt')").filter(
                has_not=page.locator("div:has-text('Add to Prompt'):has-text('Upload')")
            ),
        ),
        (
            "[aria-label*='Add to Prompt' i]",
            lambda: page.locator("[aria-label*='Add to Prompt' i]"),
        ),
    ]

    last_error: Exception | None = None
    for label, build in strategies:
        try:
            locator = build().first
            locator.wait_for(state="visible", timeout=3_000)
            log.info("Add to Prompt resolved via: %s", label)
            return locator
        except (PlaywrightTimeoutError, AssertionError) as exc:
            last_error = exc
            log.info("  strategy missed: %s (%s)", label, _short_err(exc))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.info("  strategy errored: %s (%s)", label, _short_err(exc))

    smallest = _smallest_visible_add_to_prompt(page, log)
    if smallest is not None:
        log.info("Add to Prompt resolved via: smallest-visible fallback")
        return smallest

    raise RecordedFlowError(
        f"No 'Add to Prompt' element found by any strategy. Last error: {last_error}"
    )


_ADD_TO_PROMPT_CANDIDATES_JS = r"""
() => {
  const xpath = "//*[contains(normalize-space(.), 'Add to Prompt')]";
  const res = document.evaluate(xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
  const out = [];
  for (let i = 0; i < res.snapshotLength; i++) {
    const el = res.snapshotItem(i);
    // Only keep the innermost matches — skip an ancestor if any of its
    // direct children also contains 'Add to Prompt'.
    const hasInnerMatch = Array.from(el.children).some(c =>
      (c.textContent || '').includes('Add to Prompt')
    );
    if (hasInnerMatch) continue;
    const rect = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    const visible =
      rect.width > 0 && rect.height > 0 &&
      cs.visibility !== 'hidden' && cs.display !== 'none' &&
      parseFloat(cs.opacity || '1') > 0;
    out.push({
      tag: el.tagName.toLowerCase(),
      text: ((el.innerText || el.textContent || '').trim()).slice(0, 80),
      role: el.getAttribute('role'),
      aria_label: el.getAttribute('aria-label'),
      visible: visible,
      rect: {
        x: Math.round(rect.x), y: Math.round(rect.y),
        w: Math.round(rect.width), h: Math.round(rect.height),
      },
    });
  }
  return out;
}
"""


def _diagnostics_before_add_to_prompt(page: Page, log: logging.Logger) -> None:
    # The full-page screenshot is only useful when triage'ing a missed
    # "Add to Prompt" — it adds ~50-200 ms + an IO write to every row.
    # Skip in the happy path unless DEBUG_SCREENSHOTS=true.
    if (os.environ.get("DEBUG_SCREENSHOTS") or "").strip().lower() == "true":
        try:
            _AT_PROMPT_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(_AT_PROMPT_SCREENSHOT), full_page=True)
            log.info("Saved %s", _AT_PROMPT_SCREENSHOT)
        except Exception as exc:  # noqa: BLE001
            log.warning("before_add_to_prompt_click screenshot failed: %s", exc)

    try:
        candidates = page.evaluate(_ADD_TO_PROMPT_CANDIDATES_JS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Add to Prompt candidate enumeration failed: %s", exc)
        return

    log.info("Found %d 'Add to Prompt' candidate(s):", len(candidates))
    for c in candidates:
        log.info(
            "  <%s role=%s aria=%s> %r rect=%s visible=%s",
            c.get("tag"),
            c.get("role") or "-",
            c.get("aria_label") or "-",
            c.get("text"),
            c.get("rect"),
            c.get("visible"),
        )


def _smallest_visible_add_to_prompt(page: Page, log: logging.Logger) -> Locator | None:
    """Last-resort: pick the visible element with the smallest bounding box
    whose text matches 'Add to Prompt'. Returns a Locator bound to that
    specific match (via .all() positional binding)."""
    try:
        matches = page.locator(":text('Add to Prompt')").all()
    except Exception as exc:  # noqa: BLE001
        log.warning("smallest-visible scan failed: %s", exc)
        return None

    best: Locator | None = None
    best_area = float("inf")
    for cand in matches:
        try:
            if not cand.is_visible():
                continue
            box = cand.bounding_box()
        except Exception:  # noqa: BLE001
            continue
        if not box or box["width"] <= 0 or box["height"] <= 0:
            continue
        area = box["width"] * box["height"]
        if area < best_area:
            best_area = area
            best = cand
    if best is not None:
        log.info("smallest-visible Add to Prompt area=%.0fpx²", best_area)
    return best


def _short_err(exc: Exception) -> str:
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return msg[:120]


def _locate_prompt_input(page: Page) -> Locator:
    # Recorded: input events fired on a contenteditable div at (698, 881)
    # with role="textbox" and contenteditable="true". The visible
    # "What do you want to create?" text is on a sibling <p>, not this
    # element directly, so we target the editable host instead of a
    # placeholder attribute.
    return page.locator('div[role="textbox"][contenteditable="true"]').first


def _locate_generate_arrow(page: Page) -> Locator:
    # Recorded at (1248, 918) with combined text "arrow_forward\nCreate".
    # "arrow_forward" is the Material Symbols icon ligature; unique to
    # this bottom-right send button.
    return page.locator("button").filter(has_text="arrow_forward").first


def _locate_reference_thumbnail(page: Page) -> Locator:
    # Confirmed DOM (from devtools):
    #   <button data-card-open="false" data-state="closed">
    #     <div><img src="/fx/api/trpc/media.getMediaUrlRedirect?name=..."
    #               alt="A piece of media generated or uploaded by you,
    #                    that is present in your collection." ...></div>
    #     ...
    #   </button>
    #
    # The composer is NOT a <form>; do not scope to one. Matching the
    # IMG directly is the most reliable signal that the reference has
    # been attached. We OR three selectors so any of them can resolve.
    return page.locator(
        ", ".join([
            'button img[src*="media.getMediaUrlRedirect"]',
            'img[alt*="media generated or uploaded"]',
            'img[src*="/fx/api/trpc/media.getMediaUrlRedirect"]',
        ])
    ).first


def _locate_result_images(page: Page) -> Locator:
    # Where finished images render. Used to detect a new result by
    # diffing srcs before/after generate.
    return page.locator('main img')


def _locate_tile_by_media_id(page: Page, media_id: str) -> Locator:
    """Find the result tile whose `<img src=…?name=<media_id>>` matches.

    Used by the video flow to target a specific approved image. Walks
    up from the img to the surrounding `[data-tile-id]` element so the
    returned locator points at the tile itself (suitable for hover /
    right-click / overflow-menu).
    """
    if not media_id:
        raise RecordedFlowError("media_id is empty; cannot locate tile")
    # Escape any double quotes the media_id might contain (defensive).
    safe = media_id.replace('"', '\\"')
    # `xpath=ancestor::*[@data-tile-id][1]` walks up to the nearest tile.
    return (
        page.locator(f'img[src*="name={safe}"]')
        .locator('xpath=ancestor::*[@data-tile-id][1]')
        .first
    )


def _locate_tile_overflow_button(page: Page, tile: Locator) -> Locator:
    """Three-dot overflow button on a result tile.

    Recorded as a `<button>` with combined text "more_vert\\nMore" inside
    the tile. "more_vert" is the Material Symbols icon ligature and is
    unique to this control. The button typically appears on hover —
    callers should `tile.hover()` first.
    """
    strategies: list["callable[[], Locator]"] = [
        lambda: tile.locator("button").filter(has_text="more_vert"),
        lambda: tile.locator('button[aria-label*="more" i]'),
        lambda: tile.locator('button:has(i.google-symbols)').filter(has_text="more_vert"),
    ]
    last_err: Exception | None = None
    for build in strategies:
        try:
            loc = build().first
            loc.wait_for(state="visible", timeout=3_000)
            return loc
        except (PlaywrightTimeoutError, AssertionError) as exc:
            last_err = exc
            continue
    raise RecordedFlowError(
        f"Tile overflow / three-dot button not found. Last error: {last_err}"
    )


def _locate_animate_menuitem(page: Page) -> Locator:
    """Animate menu item inside a result tile's Radix context menu.

    The menu is a Radix popover — it renders into a portal at the end
    of `<body>`, NOT inside the tile. After clicking the overflow
    button we anchor the lookup on the visible menu container (either
    `[data-radix-menu-content]` or any `[role="menu"]`), since the menu
    also contains an "Add to Prompt" menuitem which would otherwise
    occasionally win the selector race.
    """
    menu_selectors = [
        '[data-radix-menu-content]',
        '[role="menu"]',
    ]
    strategies: list["callable[[], Locator]"] = []
    for menu_sel in menu_selectors:
        strategies.append(
            lambda s=menu_sel: page.locator(
                f'{s} [role="menuitem"]:has-text("Animate")'
            )
        )
        strategies.append(
            lambda s=menu_sel: page.locator(
                f'{s} button[role="menuitem"]:has-text("Animate")'
            )
        )
    strategies += [
        lambda: page.get_by_role("menuitem", name="Animate"),
        lambda: page.locator('[role="menuitem"]:has-text("Animate")'),
        lambda: page.locator("text=/^Animate$/"),
    ]
    last_err: Exception | None = None
    for build in strategies:
        try:
            loc = build().first
            loc.wait_for(state="visible", timeout=3_000)
            return loc
        except (PlaywrightTimeoutError, AssertionError) as exc:
            last_err = exc
            continue
    raise RecordedFlowError(
        f"Animate menu item not found in the open menu. Last error: {last_err}"
    )


def _wait_for_overflow_menu(
    page: Page, timeout_ms: int = 3_000
) -> bool:
    """Wait for the Radix overflow menu to appear after the click.

    Returns True as soon as one of the known menu containers becomes
    visible. False after the timeout — caller is expected to retry.
    """
    selectors = [
        '[data-radix-menu-content]',
        '[role="menu"]',
    ]
    deadline_total = timeout_ms
    per_selector = max(300, timeout_ms // len(selectors))
    elapsed = 0
    for sel in selectors:
        try:
            page.locator(sel).first.wait_for(
                state="visible",
                timeout=min(per_selector, max(100, deadline_total - elapsed)),
            )
            return True
        except (PlaywrightTimeoutError, AssertionError):
            elapsed += per_selector
            if elapsed >= deadline_total:
                break
            continue
    return False


def _locate_generation_in_progress(page: Page) -> Locator:
    # Any loading indicator (aria-busy, progressbar, spinner) — used as
    # one signal that generate actually kicked off.
    return page.locator('[aria-busy="true"], [role="progressbar"], [data-state="loading"]')


# ---------------------------------------------------------------------------
# Google Flow risk-engine detection
# ---------------------------------------------------------------------------
#
# Flow shows several distinct error states when its anti-abuse risk
# engine decides our session is suspicious. They render on individual
# result tiles AND occasionally as page-level banners. Phrases we've
# observed in the wild:
#
#   - "We noticed some unusual activity. Please visit the Help Center
#     for more information."  (screenshot from a tester, 2026-06-05)
#   - "Too many requests. Please try again later."
#   - "Rate limit exceeded."
#   - "We were unable to complete your request right now."  (generic
#     soft-block variant — sometimes recoverable, often not)
#
# When ANY of these appear, the runner should stop the current batch
# and let the SaaS surface the failure to the user. Continuing to
# submit images would just produce more failed tiles AND raise the
# risk score further, making the cooldown longer.
#
# We detect via a single page.evaluate that walks document.body's
# innerText. Cheap (one round-trip), tolerant of DOM churn (no
# selectors), and returns a short code so the runner caller can
# discriminate per pattern when needed.

# Mapping: pattern → short error code returned to the caller.
# Order matters — first match wins so we report the most specific
# variant we can identify.
_FLOW_RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("noticed some unusual activity",   "unusual_activity"),
    ("we noticed unusual activity",     "unusual_activity"),
    ("unusual activity",                "unusual_activity"),
    ("too many requests",               "too_many_requests"),
    ("rate limit",                      "rate_limit"),
    ("try again later",                 "try_again_later"),
    ("we were unable to complete",      "soft_block"),
)


_RISK_DETECT_JS = r"""
() => {
  // Walk the body's visible text. Lowercase once for case-insensitive
  // includes(). Limit length so a runaway page doesn't blow the
  // serialization budget.
  const raw = (document.body && document.body.innerText) || '';
  return raw.slice(0, 40000).toLowerCase();
}
"""


def detect_flow_unusual_activity(page: Page) -> str | None:
    """Scan Flow's currently-rendered body text for risk-engine
    error phrases. Returns a short code (e.g. "unusual_activity",
    "rate_limit") when one matches, None when the page looks clean.

    Cheap to call — one page.evaluate per check. Designed to be
    invoked after every per-item submit in the bulk image / video
    loops so the runner can stop the batch promptly rather than
    burning more submits into a Flow tab Google is already flagging.

    Never raises — diagnostics must not be the thing that takes
    down a runner.
    """
    try:
        body_lower = page.evaluate(_RISK_DETECT_JS) or ""
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(body_lower, str) or not body_lower:
        return None
    for needle, code in _FLOW_RISK_PATTERNS:
        if needle in body_lower:
            return code
    return None


# ---------------------------------------------------------------------------
# New-project setup (runs once per day, when starting fresh)
# ---------------------------------------------------------------------------


def ensure_variants_1x(
    page: Page,
    *,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
) -> None:
    """Open the project settings popover and click the 1x variant tab.

    Idempotent: if 1x is already selected the click is a no-op, and the
    popover is closed with Escape on the way out. Best-effort — if the
    popover trigger or the 1x tab can't be found we log a warning and
    continue, never raising.

    Call this once before each batch so generations always produce a
    single tile per click (avoids the duplicate-media_id bug that
    happens when the project defaults to 2x or higher).
    """
    try:
        _locate_project_settings_trigger(page).click(timeout=selector_timeout_ms)
        logger.info("Opened project settings popover (pinning variants)")
    except (PlaywrightTimeoutError, AssertionError) as exc:
        logger.warning(
            "Settings popover trigger not found (%s); skipping variant pin.", exc
        )
        return

    try:
        _locate_variants_1x_tab(page).click(timeout=selector_timeout_ms)
        logger.info("Pinned variants = 1x")
    except (PlaywrightTimeoutError, AssertionError) as exc:
        logger.warning(
            "Could not click 1x tab (%s); continuing — variant may still be >1.",
            exc,
        )

    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass


def enter_new_project_if_present(
    page: Page,
    *,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
) -> bool:
    """If the Flow Labs landing page is showing, click "New project" and
    apply the per-project settings (aspect 9:16, 1x variant, Nano Banana
    Pro). Returns True if a new project was created, False otherwise.

    Called once at the top of `generate_image_for_product`. When the user
    is already inside an existing project (e.g. continuing the day's
    session) the New Project button is absent and we skip everything.
    """
    try:
        _locate_new_project_button(page).click(timeout=5_000)
        logger.info("Clicked New project")
    except PlaywrightTimeoutError:
        logger.info("No New project button found; staying in existing project.")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.info("New project click skipped (%s); staying in existing project.", exc)
        return False

    _apply_project_settings(page, logger=logger, selector_timeout_ms=selector_timeout_ms)
    return True


def _apply_project_settings(
    page: Page, *, logger: logging.Logger, selector_timeout_ms: int
) -> None:
    """Open the composer settings popover and pick aspect/variant/model.

    Each click is best-effort: if a setting is already at the desired
    value, the click is harmless; if a locator misses we log and continue
    rather than failing the whole run.
    """
    try:
        _locate_project_settings_trigger(page).click(timeout=selector_timeout_ms)
        logger.info("Opened project settings popover")
    except (PlaywrightTimeoutError, AssertionError) as exc:
        logger.warning(
            "Project settings popover trigger not found (%s); skipping aspect/"
            "variant/model selection. Re-record if Flow's UI has changed.", exc
        )
        return

    # The popover defaults to whichever mode the composer was last
    # in. A fresh "New project" or a recent video flow can land it on
    # the Video tab — and in that state the Nano Banana Pro model is
    # absent from the DOM AND the composer doesn't render the upload
    # + button. We MUST flip the composer back to image mode before
    # proceeding. If we can't, log a clear error and skip the
    # aspect/variant/model loop instead of pressing on into guaranteed
    # downstream timeouts.
    pre_mode = _composer_is_in_image_mode(page)
    if pre_mode is True:
        logger.info("Composer already in image mode")
    else:
        if pre_mode is False:
            logger.info(
                "Composer is in video mode — switching to image mode",
            )
        else:
            logger.info(
                "Composer mode could not be determined — attempting "
                "image-mode switch anyway",
            )
        switched = False
        try:
            image_tab = _locate_image_mode_tab(page)
            if image_tab.is_visible(timeout=1_000):
                image_tab.click(timeout=selector_timeout_ms)
                logger.info("Clicked Image-mode toggle in settings popover")
                page.wait_for_timeout(350)
                switched = True
        except (PlaywrightTimeoutError, AssertionError) as exc:
            logger.warning(
                "Image-mode toggle not visible / not clickable: %s", exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Image-mode toggle click raised: %s", exc,
            )

        # Verify the switch took. If it didn't, abort the rest of
        # this helper so the caller gets a single clean warning
        # instead of three separate "could not select X" timeouts.
        post_mode = _composer_is_in_image_mode(page)
        if post_mode is True:
            logger.info("Composer is now in image mode")
        else:
            logger.warning(
                "Composer is NOT in image mode after switch attempt "
                "(pre=%s, post=%s, switched=%s). Skipping aspect / "
                "variant / model selection; downstream upload step "
                "will fail with a clearer error.",
                pre_mode, post_mode, switched,
            )
            # Close the popover so we don't leave it dangling for
            # the upload step.
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            return

    # Aspect + variants are simple role="tab" clicks — they're always
    # in the DOM regardless of selection state.
    for label, locator_fn in (
        ("aspect 9:16", _locate_aspect_9_16_tab),
        ("1x variants", _locate_variants_1x_tab),
    ):
        try:
            locator_fn(page).click(timeout=selector_timeout_ms)
            logger.info("Selected %s", label)
        except (PlaywrightTimeoutError, AssertionError) as exc:
            logger.warning("Could not select %s (%s); continuing.", label, exc)

    # Model is a dropdown, not a tab. If the user has manually
    # picked a different image model (Nano Banana 2, Imagen, etc.)
    # the Nano Banana Pro option only appears in the DOM after the
    # dropdown opens — a direct click on a "Nano Banana Pro" span
    # times out because nothing matches. _pin_image_model handles
    # the open→pick→verify dance.
    _pin_image_model(
        page,
        logger=logger,
        target_name="Nano Banana Pro",
        selector_timeout_ms=selector_timeout_ms,
    )

    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def perform_recorded_flow(
    page: Page,
    image_path: str,
    prompt: str,
    *,
    additional_image_paths: list[str] | None = None,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
    generation_timeout_seconds: int = 180,
    verify_generation_started: bool = False,
    wait_for_result: bool = False,
    capture_tile: bool = True,
    capture_timeout_seconds: int = 15,
    capture_sibling_window_ms: int = 2000,
    fast_submit_mode: bool = True,
    debug_screenshots: bool = False,
) -> list[TileInfo]:
    """Run one Flow Labs generation through the click-generate step.

    Success contract: reference thumbnail(s) attached + prompt inserted
    + generate arrow clicked. After the click we collect (best-effort)
    every new tile that appears within `capture_timeout_seconds` so we
    persist all variant `flow_media_id`s onto the CSV row — the user
    may heart any variant in Flow Labs and we want sync-favorites to
    match regardless of which one.

    Phase 3 — multi-reference support: `image_path` is the primary
    reference; `additional_image_paths` is an optional ordered list of
    extra references (ref2, ref3) attached by repeating the same
    upload → "+" → "Add to Prompt" sequence. Flow's composer accepts
    multiple thumbnails — confirmed via the user's UI walkthrough
    (clicking "+" again or drag/drop both work; we use "+").

    Returns the list of captured TileInfos, or an empty list when
    capture is disabled or nothing appeared in time. Raises
    RecordedFlowError on hard step failures.
    """
    prior_result_srcs = _snapshot_result_srcs(page) if wait_for_result else set()
    prior_tile_ids = _snapshot_tile_ids(page) if capture_tile else set()

    # Build the ordered list of images to attach. Primary first,
    # supplementary refs after in the order the SaaS sent them.
    all_image_paths: list[str] = [image_path] + list(additional_image_paths or [])

    # --- Attach each reference image in turn. Steps 1-3 are repeated
    # for each image; the prompt + submit happens once at the end.
    add_to_prompt_enable_timeout_ms = max(selector_timeout_ms * 3, 45_000)
    for ref_idx, ref_path in enumerate(all_image_paths, start=1):
        role_label = "primary" if ref_idx == 1 else f"ref{ref_idx}"
        logger.info(
            "Attaching reference image %d/%d (%s): %s",
            ref_idx, len(all_image_paths), role_label, ref_path,
        )

        # --- 1. Upload this reference image ---
        _locate_file_input(page).set_input_files(ref_path)
        logger.info("[%s] Uploaded image", role_label)

        # --- 2. Click "+" in composer ---
        _locate_plus_button(page).click(timeout=selector_timeout_ms)
        logger.info("[%s] Clicked composer plus button", role_label)

        # --- 3. Click "Add to Prompt", then confirm the thumbnail
        #
        # The "Add to Prompt" button stays `disabled` until Flow's
        # backend finishes processing the upload from step 1. Most
        # products clear in under a second, but every now and then
        # (large image, slow server response, image format that
        # needs a transcode step) Flow holds the button disabled
        # for several seconds. The default Playwright .click()
        # timeout is `selector_timeout_ms` (15s), which is the same
        # value across safe/balanced/fast — when that's not enough
        # we get a clean failure with "element is not enabled"
        # across ~30 retries.
        #
        # Fix: explicitly wait for the button to become enabled
        # with a longer budget than the click timeout, so a slow
        # upload doesn't take the whole batch with it. We also re-
        # locate after the wait because Flow occasionally swaps the
        # underlying element between the disabled and enabled state,
        # which can stale-element the locator we had a moment ago.
        try:
            expect(_locate_add_to_prompt(page)).to_be_enabled(
                timeout=add_to_prompt_enable_timeout_ms
            )
        except (AssertionError, PlaywrightTimeoutError) as exc:
            raise RecordedFlowError(
                f"[{role_label}] 'Add to Prompt' button stayed disabled "
                f"for {add_to_prompt_enable_timeout_ms / 1000:.0f}s — Flow "
                "is probably still processing the uploaded image, or the "
                "upload itself failed. Try the row again."
            ) from exc
        _locate_add_to_prompt(page).click(timeout=selector_timeout_ms)

        # After the click, confirm at least `ref_idx` thumbnails are
        # present. Counting is the cleanest cross-check: a multi-ref
        # attach where the second image silently failed would still
        # show one thumbnail; only the count tells us all attaches
        # landed.
        try:
            expect(_locate_reference_thumbnail(page)).to_have_count(
                ref_idx, timeout=selector_timeout_ms
            )
        except (AssertionError, PlaywrightTimeoutError) as exc:
            raise RecordedFlowError(
                f"[{role_label}] Expected {ref_idx} reference thumbnail(s) "
                f"after Add to Prompt; count assertion failed. The "
                "secondary attach path may not have wired up — verify "
                "Flow's UI hasn't changed."
            ) from exc
        logger.info(
            "[%s] Clicked Add to Prompt — %d thumbnail(s) attached",
            role_label, ref_idx,
        )

    # --- 4. Type prompt, confirm it is visible in the composer ---
    prompt_input = _locate_prompt_input(page)
    prompt_input.click()
    prompt_input.fill(prompt)
    if not _is_prompt_text_present(prompt_input, prompt):
        raise RecordedFlowError(
            "Prompt text not visible in composer after fill(); refusing to "
            "log 'Prompt inserted'."
        )
    logger.info("Prompt inserted")

    # --- 5. Confirm arrow is enabled and click ---
    arrow = _locate_generate_arrow(page)
    try:
        expect(arrow).to_be_enabled(timeout=selector_timeout_ms)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        raise RecordedFlowError("Generate arrow is not enabled.") from exc

    arrow.click()
    logger.info("Generate clicked")

    if verify_generation_started:
        if not _generation_started(page, prior_result_srcs, timeout_ms=10_000):
            raise RecordedFlowError(
                "Clicked the arrow but no generation activity (new result "
                "tile, loading indicator, or generation request) was "
                "detected within 10s."
            )
        logger.info("Generation activity detected")

    # --- 6. Best-effort: capture EVERY new tile that appears so we
    #        store all variant media_ids on the CSV row. We do not wait
    #        for image content to finish rendering — only for new
    #        `[data-tile-id]` elements to appear.
    captured: list[TileInfo] = []
    if capture_tile:
        # Fast-submit (default): return after Phase 1 with tile_ids only.
        # The orchestration layer sweeps the gallery once after the
        # whole batch finishes and fills in media_ids. ~10-20x faster
        # per row than waiting for the media URL inline.
        captured = _wait_for_new_tiles(
            page,
            prior_tile_ids,
            timeout_seconds=capture_timeout_seconds,
            sibling_window_ms=capture_sibling_window_ms,
            require_media_id=not fast_submit_mode,
        )
        if captured:
            ids = ", ".join(
                (t.flow_media_id or t.flow_tile_id)[:8] for t in captured
            )
            logger.info("Captured %d tile(s): %s", len(captured), ids)
        else:
            logger.info(
                "No new tile observed within %ds (continuing).",
                capture_timeout_seconds,
            )

    if not wait_for_result:
        return captured

    # If we already have a captured tile with a media URL, use that.
    for tile in captured:
        if tile.flow_image_src:
            return captured

    new_src = _wait_for_new_result(
        page, prior_result_srcs, timeout_seconds=generation_timeout_seconds
    )
    if not new_src:
        raise RecordedFlowError(
            f"No new result image appeared within {generation_timeout_seconds}s."
        )
    logger.info("New result image detected")
    return [TileInfo(flow_image_src=new_src)]


# ---------------------------------------------------------------------------
# State checks
# ---------------------------------------------------------------------------


def _is_prompt_text_present(prompt_input: Locator, expected: str) -> bool:
    """Confirm the composer actually contains the prompt text.

    Works for <textarea>/<input> (input_value) and contenteditable
    (inner_text). We compare a short prefix to tolerate trailing
    whitespace or rich-text wrapping.
    """
    needle = expected.strip()[:60]
    if not needle:
        return False
    for getter in ("input_value", "inner_text"):
        try:
            value = getattr(prompt_input, getter)()
        except Exception:  # noqa: BLE001
            continue
        if value and needle in value:
            return True
    return False


def _snapshot_result_srcs(page: Page) -> set[str]:
    try:
        srcs = _locate_result_images(page).evaluate_all(
            "elements => elements.map(e => e.getAttribute('src') || '').filter(Boolean)"
        )
        return set(srcs)
    except Exception:  # noqa: BLE001
        return set()


def _generation_started(page: Page, prior: Iterable[str], timeout_ms: int) -> bool:
    """Return True iff some side-effect of clicking generate is observable.

    We accept any of: a new <img> src in the result area, a visible
    loading indicator, or no-op on an already-busy page.
    """
    prior_set = set(prior)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if _snapshot_result_srcs(page) - prior_set:
            return True
        try:
            if _locate_generation_in_progress(page).first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(400)
    return False


def _wait_for_new_result(
    page: Page, prior: Iterable[str], timeout_seconds: int
) -> str | None:
    """Return the first src that wasn't in `prior` once generation completes.

    Picks the longest new src on the assumption that signed/full URLs
    are longer than placeholder/preview ones.
    """
    prior_set = set(prior)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = _snapshot_result_srcs(page)
        new = [s for s in current - prior_set if s]
        if new:
            # Also require the in-progress indicator to be gone, so we
            # don't grab a placeholder mid-render.
            try:
                if _locate_generation_in_progress(page).first.is_visible(timeout=200):
                    page.wait_for_timeout(750)
                    continue
            except (PlaywrightTimeoutError, Exception):  # noqa: BLE001
                pass
            new.sort(key=len, reverse=True)
            return new[0]
        page.wait_for_timeout(750)
    return None


def _snapshot_tile_ids(page: Page) -> set[str]:
    """Return the set of `data-tile-id` values currently in the DOM."""
    try:
        ids = page.locator("[data-tile-id]").evaluate_all(
            "elements => elements.map(e => e.getAttribute('data-tile-id') || '').filter(Boolean)"
        )
        return set(ids)
    except Exception:  # noqa: BLE001
        return set()


def _wait_for_new_tiles(
    page: Page,
    prior_ids: set[str],
    timeout_seconds: int,
    sibling_window_ms: int = 2000,
    require_media_id: bool = True,
) -> list[TileInfo]:
    """Capture new tiles after a generate click.

    Phase 1 — always: poll until at least one new ``data-tile-id``
    appears, then keep polling an extra ``sibling_window_ms`` to
    enumerate sibling variant tile_ids.

    Phase 2 — only when ``require_media_id=True``: keep polling until
    every tracked tile_id has a non-empty ``flow_media_id``. This is
    the slow path; the media URL only appears after Flow has rendered
    the result (5-60 s).

    Fast-submit mode (``require_media_id=False``) returns after Phase 1
    with whatever scan_tiles can read — tile_id is filled (instant),
    flow_media_id may be empty. The orchestration layer is expected to
    run a final ``_sweep_fill_media_ids`` pass once the whole batch
    has submitted, binding media_ids by tile_id.
    """
    deadline = time.monotonic() + timeout_seconds
    new_tile_ids: set[str] = set()
    first_seen_at: float | None = None

    # Phase 1: enumerate sibling tile_ids.
    while time.monotonic() < deadline:
        try:
            tiles = scan_tiles(page)
        except Exception:  # noqa: BLE001
            tiles = []
        for t in tiles:
            if not t.flow_tile_id or t.flow_tile_id in prior_ids:
                continue
            if t.flow_tile_id not in new_tile_ids:
                new_tile_ids.add(t.flow_tile_id)
                if first_seen_at is None:
                    first_seen_at = time.monotonic()
        if first_seen_at is not None and (
            (time.monotonic() - first_seen_at) * 1000 >= sibling_window_ms
        ):
            break
        page.wait_for_timeout(500)

    if not new_tile_ids:
        return []

    # Fast-submit: skip Phase 2. Return whatever scan_tiles sees now;
    # the orchestration layer fills in media_ids in one final sweep.
    if not require_media_id:
        try:
            tiles = scan_tiles(page)
        except Exception:  # noqa: BLE001
            tiles = []
        return [t for t in tiles if t.flow_tile_id in new_tile_ids]

    # Phase 2: wait for media_ids on each tracked tile_id.
    while time.monotonic() < deadline:
        try:
            tiles = scan_tiles(page)
        except Exception:  # noqa: BLE001
            tiles = []
        matched = [t for t in tiles if t.flow_tile_id in new_tile_ids]
        with_media = [t for t in matched if t.flow_media_id]
        if len(with_media) >= len(new_tile_ids):
            return with_media
        page.wait_for_timeout(500)

    # Timeout: return whatever has a media_id so far.
    try:
        tiles = scan_tiles(page)
    except Exception:  # noqa: BLE001
        tiles = []
    return [t for t in tiles if t.flow_tile_id in new_tile_ids and t.flow_media_id]


def sweep_fill_media_ids(
    page: Page,
    tile_ids_by_row: dict[int, list[str]],
    logger: logging.Logger,
) -> dict[int, list[TileInfo]]:
    """Final post-batch sweep — bind media_ids to row indexes by tile_id.

    Used by fast-submit mode. Returns a dict keyed by row index with
    the TileInfo objects (including resolved media_ids and srcs) that
    matched each row's stored tile_ids. The caller writes them onto
    the CSV row.
    """
    try:
        tiles = scan_tiles(page)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-batch sweep failed to scan_tiles: %s", exc)
        return {}
    by_tile_id: dict[str, TileInfo] = {
        t.flow_tile_id: t for t in tiles if t.flow_tile_id
    }
    out: dict[int, list[TileInfo]] = {}
    for idx, tile_ids in tile_ids_by_row.items():
        matched: list[TileInfo] = []
        for tid in tile_ids:
            t = by_tile_id.get(tid)
            if t is not None:
                matched.append(t)
        if matched:
            out[idx] = matched
    return out


# ---------------------------------------------------------------------------
# Video flow (image → animate → video)
# ---------------------------------------------------------------------------


def _scroll_tile_to_center(page: Page, tile: Locator, logger: logging.Logger) -> None:
    """Scroll the tile to the vertical center of the viewport.

    Two things matter:
      1. The tile shouldn't be under any sticky header (Flow has one;
         the header's search input would otherwise intercept clicks).
      2. scrollIntoView with block: 'center' tends to behave better than
         scrollIntoViewIfNeeded because it ignores the IfNeeded guard
         and unconditionally re-centres.
    """
    try:
        tile.evaluate(
            "el => el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'})"
        )
    except Exception:  # noqa: BLE001
        try:
            tile.scroll_into_view_if_needed()
        except Exception:  # noqa: BLE001
            return
    # Belt-and-suspenders: if the tile is still inside the top header
    # band, scroll up further.
    try:
        box = tile.bounding_box()
        if box and box["y"] < 120:
            offset = int(box["y"]) - 220
            page.evaluate("(o) => window.scrollBy(0, o)", offset)
    except Exception:  # noqa: BLE001
        pass


def _click_with_fallback(
    page: Page,
    locator: Locator,
    label: str,
    logger: logging.Logger,
    timeout_ms: int = 3_000,
) -> str | None:
    """Try several click methods in order; return the method name that worked.

    Order:
        1. Native Playwright click — most realistic.
        2. Force click — bypasses actionability checks (covers
           "element is unstable" / "another element intercepts events").
        3. JS .click() — fires a synthetic click directly on the node.
        4. Coordinate click on the bounding-box centre via page.mouse.
    Returns None when every method failed.
    """
    # IMPORTANT: every method needs an explicit timeout. Locator
    # operations without a `timeout=` argument default to Playwright's
    # global 30s, which on a sequence of 4 fallbacks means a single
    # attempt can hang for >2 minutes — observed live on 2026-06-04
    # where the overflow click stalled 67 seconds before retry-2
    # succeeded instantly with a fresh hover. Caller passes the budget;
    # we apply it uniformly so retry-and-rehover wins the race.
    methods: list[tuple[str, callable]] = [
        ("playwright_click", lambda: locator.click(timeout=timeout_ms)),
        ("force_click",      lambda: locator.click(timeout=timeout_ms, force=True)),
        ("js_click",         lambda: locator.evaluate(
            "el => el.click()", timeout=timeout_ms,
        )),
    ]
    for name, fn in methods:
        try:
            fn()
            logger.info("Clicked %s via %s", label, name)
            return name
        except Exception as exc:  # noqa: BLE001
            logger.info("  %s/%s failed: %s", label, name, _short_err(exc))

    # Last resort: synthesize a coordinate click on the centre of the box.
    try:
        box = locator.bounding_box(timeout=timeout_ms)
        if box and box["width"] > 0 and box["height"] > 0:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            page.mouse.click(cx, cy)
            logger.info("Clicked %s via coordinate (%.0f, %.0f)", label, cx, cy)
            return "coordinate"
    except Exception as exc:  # noqa: BLE001
        logger.info("  %s/coordinate failed: %s", label, _short_err(exc))
    return None


def _save_step_screenshot(
    page: Page,
    logs_dir: Path | None,
    name: str,
    media_id: str,
    logger: logging.Logger,
) -> None:
    """Best-effort screenshot for video-flow debugging.

    `name` becomes part of the filename (e.g. before_video_overflow). If
    `logs_dir` is None or the screenshot fails, we just log a warning —
    never raise.
    """
    if logs_dir is None:
        return
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        safe_id = media_id.replace("/", "_")[:36]
        path = logs_dir / f"{name}_{safe_id}.png"
        page.screenshot(path=str(path), full_page=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save %s screenshot: %s", name, exc)


def perform_recorded_video_flow(
    page: Page,
    media_id: str,
    video_prompt: str,
    *,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
    tile_settle_ms: int = 800,
    after_hover_ms: int = 800,
    after_menu_click_ms: int = 800,
    retry_count: int = 3,
    logs_dir: Path | None = None,
    debug_screenshots: bool = False,
) -> None:
    """Animate a previously generated image identified by `media_id`.

    Hardened against the common failure modes on Flow's gallery view:

      * Tile under the sticky header → search input intercepts clicks.
        Mitigation: `scrollIntoView({block: 'center'})` + a secondary
        scroll up if the tile is still inside the top band.

      * Tile DOM mutates between hover and click → Playwright reports
        "element became unstable" and aborts. Mitigation: re-locate the
        overflow button after every hover and use a click-method
        fallback chain that includes force + JS + coordinate clicks.

      * `after_hover_ms` and `tile_settle_ms` waits give Flow time to
        finish the hover-driven CSS transitions that swap the
        more_vert button in/out.

    Configurable via the matching VIDEO_* env vars; see config.py.
    Raises RecordedFlowError on any state-check failure so the
    caller can mark the row video_error and move on without
    derailing the batch.
    """
    if not media_id:
        raise RecordedFlowError("media_id is empty; cannot start video flow")
    if not video_prompt:
        raise RecordedFlowError("video_prompt is empty; refusing to submit")

    # Make sure any lingering menu from a previous iteration is gone.
    try:
        page.keyboard.press("Escape")
        page.mouse.move(0, 0)
    except Exception:  # noqa: BLE001
        pass

    # --- 1. Locate the tile ------------------------------------------------
    tile = _locate_tile_by_media_id(page, media_id)
    try:
        tile.wait_for(state="visible", timeout=selector_timeout_ms)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        raise RecordedFlowError(
            f"Tile for media_id={media_id} not visible in the gallery."
        ) from exc
    logger.info("Tile found media_id=%s", media_id)

    # --- 2. Center the tile in the viewport --------------------------------
    _scroll_tile_to_center(page, tile, logger)
    page.wait_for_timeout(tile_settle_ms)
    logger.info("Tile scrolled to center (settle=%dms)", tile_settle_ms)

    # --- 3. Open the overflow menu, with retry --------------------------------
    overflow_clicked_method: str | None = None
    last_overflow_error: str = ""
    max_overflow_attempts = max(1, retry_count)
    for attempt in range(1, max_overflow_attempts + 1):
        # Re-center + re-hover between every attempt. Stale locators in
        # particular survive longer than they should, so we re-locate
        # the overflow button after every hover.
        try:
            _scroll_tile_to_center(page, tile, logger)
            page.wait_for_timeout(tile_settle_ms)
            tile.hover()
            page.wait_for_timeout(after_hover_ms)
            logger.info(
                "Tile hovered (attempt %d/%d, after_hover=%dms)",
                attempt, max_overflow_attempts, after_hover_ms,
            )

            if debug_screenshots:
                _save_step_screenshot(
                    page, logs_dir, "before_video_overflow", media_id, logger,
                )

            overflow = _locate_tile_overflow_button(page, tile)
            try:
                overflow.wait_for(state="visible", timeout=5_000)
            except (AssertionError, PlaywrightTimeoutError) as exc:
                last_overflow_error = f"overflow not visible: {_short_err(exc)}"
                logger.info("  attempt %d: %s", attempt, last_overflow_error)
                # Reset state for next attempt.
                page.mouse.move(0, 0)
                page.wait_for_timeout(300)
                continue

            logger.info("Overflow button visible — trying click")
            method = _click_with_fallback(page, overflow, "overflow", logger)
            if method is not None:
                overflow_clicked_method = method
                break
            last_overflow_error = "all click methods failed"
        except Exception as exc:  # noqa: BLE001
            last_overflow_error = _short_err(exc)
            logger.warning(
                "Overflow attempt %d/%d raised: %s",
                attempt, max_overflow_attempts, last_overflow_error,
            )

        # Reset between retries.
        try:
            page.mouse.move(0, 0)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(500)

    if overflow_clicked_method is None:
        raise RecordedFlowError(
            f"Could not open overflow menu for media_id={media_id} after "
            f"{max_overflow_attempts} attempts. Last: {last_overflow_error}"
        )

    # --- 4. Wait for the menu to actually render, then locate Animate ------
    # Prefer a targeted DOM wait over a fixed sleep — the menu usually
    # paints in under 100 ms; the post-click delay is only there as a
    # backstop.
    if not _wait_for_overflow_menu(page, timeout_ms=max(after_menu_click_ms, 2000)):
        logger.warning(
            "Overflow menu container did not appear after click; falling "
            "back to a static %dms sleep and trying the Animate locator anyway.",
            after_menu_click_ms,
        )
        page.wait_for_timeout(after_menu_click_ms)
    else:
        logger.info("Overflow menu visible")

    if debug_screenshots:
        _save_step_screenshot(page, logs_dir, "after_video_menu", media_id, logger)

    animate = _locate_animate_menuitem(page)
    try:
        animate.wait_for(state="visible", timeout=selector_timeout_ms)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        raise RecordedFlowError(
            "Animate menuitem did not appear after opening overflow menu."
        ) from exc
    logger.info("Animate menuitem visible")
    if _click_with_fallback(page, animate, "Animate", logger) is None:
        raise RecordedFlowError("Animate click failed across all methods.")

    # --- 4b. Pin the video model to "Veo 3.1 - Lite" ----------------------
    # Flow defaults some sessions to "Omni Flash" — a text-to-video
    # model that ignores the reference image. When that happens, the
    # favorited tile we just promoted into the composer is discarded
    # and the generation comes back without the source content.
    # Veo 3.1 - Lite is the image-to-video model we want.
    #
    # Best-effort: the helper never raises, so a Flow UI change here
    # logs a warning and the submit proceeds with whatever model was
    # previously selected (better than aborting the whole batch).
    # Late import — flow_ui_prep imports from this module, so the
    # top-level import would be circular.
    try:
        from .flow_ui_prep import ensure_veo_lite_model
        model_report = ensure_veo_lite_model(
            page, logger=logger,
            selector_timeout_ms=selector_timeout_ms,
        )
        if model_report and not model_report.get("skipped") and model_report.get("model_now"):
            logger.info(
                "video model pinned to %r (was %r)",
                model_report.get("model_now"),
                model_report.get("model_was") or "(unknown)",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "flow-ui-prep: video model pin raised non-fatal error: %s",
            exc,
        )

    # --- 5. Fill the video prompt -----------------------------------------
    prompt_input = _locate_prompt_input(page)
    try:
        prompt_input.wait_for(state="visible", timeout=selector_timeout_ms)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        raise RecordedFlowError(
            "Video composer prompt input did not appear after Animate."
        ) from exc
    prompt_input.click()
    prompt_input.fill(video_prompt)
    if not _is_prompt_text_present(prompt_input, video_prompt):
        raise RecordedFlowError(
            "Video prompt not visible in composer after fill(); refusing "
            "to click generate."
        )
    logger.info("Video prompt inserted")

    # --- 6. Click the generate arrow --------------------------------------
    arrow = _locate_generate_arrow(page)
    try:
        expect(arrow).to_be_enabled(timeout=selector_timeout_ms)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        raise RecordedFlowError("Video generate arrow is not enabled.") from exc
    if _click_with_fallback(page, arrow, "video generate arrow", logger) is None:
        raise RecordedFlowError("Video generate-arrow click failed across all methods.")
    logger.info("Video generate clicked")
