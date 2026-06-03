"""Centralized Flow UI prep — dismiss overlays + verify settings.

Called by every automation path that submits a prompt to Google
Flow:

  - src.agent_api._handle_generate_flow_images
  - src.agent_api._handle_generate_flow_videos_from_favorites
  - the standalone runner (runner_app.py) inherits both via the
    same handler table.

What "prep" means here:

  1. Dismiss whatever stale UI Flow left over from a previous step
     — menus, pills, overlays, side panels, agent-prompt suggestion
     chips. Idempotent and best-effort: if a thing isn't there,
     keep going.
  2. Verify (and where possible enforce) the generation settings
     the automation expects — image mode with 9:16 / 1x / Nano
     Banana Pro; for video, just ensure no stale composer is open
     before the per-tile Animate click sequence.

Design rules:

  - **Never raise** out of the public functions. Prep is informational.
    A failed dismiss must not cancel a real submit.
  - **Never click destructive buttons** ("Delete", "Discard",
    "Leave", "Confirm"). Only close/dismiss/cancel controls + the
    Escape key.
  - **Short timeouts** for optional UI cleanup — don't burn 15s
    looking for a menu that isn't open. The locator-level finds
    use ~500–2000ms.
  - **Reuse proven low-level helpers** from `src.recorded_flow` for
    aspect/variant/model settings. Don't re-invent selectors.

Toggleable via env (operator escape hatches, not exposed in the
SaaS UI):

  FLOW_UI_PREP_ENABLED               default true
  FLOW_DISMISS_OVERLAYS              default true
  FLOW_ENSURE_GENERATION_SETTINGS    default true
  DEBUG_FLOW_PREP                    default false — verbose logging
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only imported for type hints — Playwright isn't a hard runtime
    # dep of this module's import surface so health_check / diagnose
    # can pull `flow_ui_prep` for `is_prep_enabled()` without a
    # browser stack.
    from playwright.sync_api import Page


# ---------------------------------------------------------------------
# Env toggles
# ---------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def is_prep_enabled() -> bool:
    return _env_bool("FLOW_UI_PREP_ENABLED", True)


def is_dismiss_overlays_enabled() -> bool:
    return _env_bool("FLOW_DISMISS_OVERLAYS", True)


def is_ensure_settings_enabled() -> bool:
    return _env_bool("FLOW_ENSURE_GENERATION_SETTINGS", True)


def is_debug() -> bool:
    return _env_bool("DEBUG_FLOW_PREP", False)


# ---------------------------------------------------------------------
# Step 1 — dismiss stale UI clutter
# ---------------------------------------------------------------------

def dismiss_flow_overlays(
    page: "Page",
    *,
    logger: logging.Logger,
    settle_ms: int = 150,
) -> dict:
    """Clear stale menus / dialogs / agent panels so the next action
    isn't intercepted by a leftover overlay. Returns a small dict
    summarising what was found, useful for tests and the diagnose
    path's dry-run.

    Order matters:

      1. Escape — closes most Radix menus, comboboxes, dialogs, and
         tooltips without needing a precise selector. Cheap, safe.
      2. Mouse to corner — defocuses any hover-driven popover that
         Flow renders only while the originating element is hovered.
      3. Targeted aria-label close buttons — anything labelled
         close / dismiss / cancel in the document right now. Skips
         labels that look destructive.
      4. Open Radix menu containers — if a menu DOM is still
         attached, press Escape again from inside it.
    """
    if not is_dismiss_overlays_enabled():
        if is_debug():
            logger.info("flow-ui-prep: dismiss disabled by env")
        return {"skipped": True}

    seen = {
        "escape_first": False,
        "mouse_corner": False,
        "close_buttons_clicked": 0,
        "menus_closed": 0,
    }

    # 1. Escape — first pass. Wrapped because keyboard.press can
    #    raise PlaywrightError on a brand-new page that hasn't
    #    focused anything yet.
    try:
        page.keyboard.press("Escape")
        seen["escape_first"] = True
    except Exception:  # noqa: BLE001
        pass

    # 2. Mouse to the corner to drop any hover-only popover.
    try:
        page.mouse.move(0, 0)
        seen["mouse_corner"] = True
    except Exception:  # noqa: BLE001
        pass

    page.wait_for_timeout(settle_ms)

    # 3. aria-label / role-name close buttons. We accept any
    #    visible button whose accessible name matches /close|dismiss
    #    |cancel/i AND does NOT match anything destructive. Cap at
    #    5 clicks per pass — Flow rarely stacks more, and an infinite
    #    loop on a misclassified label would be worse than leaving
    #    one overlay open.
    SAFE = ("close", "dismiss", "cancel")
    DESTRUCTIVE = (
        "delete", "remove", "leave", "discard", "confirm",
        "sign out", "log out",
    )
    clicked = 0
    try:
        # Limit candidate set so a hostile page can't make this loop
        # forever. 16 is plenty for any sane Flow state.
        candidates = page.locator(
            "button[aria-label], button[title], [role='button'][aria-label]",
        )
        n = min(candidates.count(), 16)
        for i in range(n):
            if clicked >= 5:
                break
            try:
                el = candidates.nth(i)
                if not el.is_visible(timeout=200):
                    continue
                label = ""
                try:
                    label = (el.get_attribute("aria-label") or "").lower()
                except Exception:  # noqa: BLE001
                    pass
                if not label:
                    try:
                        label = (el.get_attribute("title") or "").lower()
                    except Exception:  # noqa: BLE001
                        pass
                if not label:
                    continue
                if any(bad in label for bad in DESTRUCTIVE):
                    continue
                if not any(good in label for good in SAFE):
                    continue
                el.click(timeout=500)
                clicked += 1
                if is_debug():
                    logger.info(
                        "flow-ui-prep: clicked close/dismiss button "
                        "(aria-label=%r)", label,
                    )
            except Exception:  # noqa: BLE001
                # A locator can go stale between is_visible and
                # click; just skip it.
                continue
    except Exception as exc:  # noqa: BLE001
        if is_debug():
            logger.info("flow-ui-prep: close-buttons sweep error: %s", exc)
    seen["close_buttons_clicked"] = clicked

    # 4. Any remaining Radix-style menu? Press Escape once more.
    try:
        menus = page.locator(
            "[data-radix-menu-content], [role='menu'], [role='dialog']"
        )
        visible_menus = 0
        for i in range(min(menus.count(), 4)):
            try:
                if menus.nth(i).is_visible(timeout=150):
                    visible_menus += 1
            except Exception:  # noqa: BLE001
                continue
        if visible_menus:
            try:
                page.keyboard.press("Escape")
                seen["menus_closed"] = visible_menus
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        if is_debug():
            logger.info("flow-ui-prep: menu sweep error: %s", exc)

    if any([
        seen["close_buttons_clicked"],
        seen["menus_closed"],
    ]):
        logger.info(
            "Flow UI prep dismissed: %d close button(s), %d menu(s)",
            seen["close_buttons_clicked"], seen["menus_closed"],
        )
    elif is_debug():
        logger.info("Flow UI prep: nothing to dismiss")
    return seen


def close_agent_prompt_pills(
    page: "Page",
    *,
    logger: logging.Logger,
) -> dict:
    """Close any "Agent prompt suggestion" pills / chips that Flow
    sometimes drops above the composer. These intercept clicks on
    the prompt input and can wedge the Generate button.

    Strategy:
      - Find buttons whose accessible name is exactly "close" /
        ✕ / × inside a parent that looks like an agent-prompt chip.
        Flow has used several class hashes for these; we anchor on
        the close glyph rather than the container.
      - Also dismiss any element with role=button + aria-pressed
        whose label includes "agent" — the toggle pill that opens
        the agent panel.
    """
    closed = 0
    # Material Symbols close ligature is the most reliable signal —
    # Flow renders it inside an `<i class="google-symbols">close</i>`
    # element wrapped in a button or div that's clickable.
    try:
        icons = page.locator("i.google-symbols", has_text="close")
        n = min(icons.count(), 8)
        for i in range(n):
            try:
                el = icons.nth(i)
                if not el.is_visible(timeout=200):
                    continue
                # Don't click inside a destructive container. We
                # check the nearest ancestor button/div's text for
                # destructive words.
                container = el.locator(
                    "xpath=ancestor::*[self::button or self::div][1]",
                ).first
                container_text = ""
                try:
                    container_text = (container.inner_text(timeout=200) or "").lower()
                except Exception:  # noqa: BLE001
                    pass
                if any(
                    bad in container_text
                    for bad in ("delete", "discard", "leave", "confirm")
                ):
                    continue
                container.click(timeout=500)
                closed += 1
                if is_debug():
                    logger.info(
                        "flow-ui-prep: closed agent prompt chip "
                        "(container text=%r)", container_text[:60],
                    )
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        if is_debug():
            logger.info("flow-ui-prep: agent pill sweep error: %s", exc)

    if closed:
        logger.info("Flow UI prep closed %d agent prompt pill(s)", closed)
    elif is_debug():
        logger.info("Flow UI prep: no agent prompt pills found")
    return {"agent_pills_closed": closed}


def toggle_off_agent_mode(
    page: "Page",
    *,
    logger: logging.Logger,
) -> dict:
    """Toggle Flow's composer out of *Agent mode* if it's currently on.

    Flow's composer has an "Agent" pill next to the `+` button. When
    pressed (`aria-pressed="true"`), the Generate arrow runs the
    Agent flow instead of the standard image-generation flow — the
    runner's recorded selectors all assume the standard flow, so a
    pressed Agent pill silently breaks every submit.

    Visible symptom (matches the user's screenshots):
      - "Hi <name> / What would you like to do?" landing screen
        with three preset action buttons.
      - A white "Agent" pill at the composer's bottom-left.

    We:
      1. Find any visible `button[aria-pressed="true"]` whose text
         is exactly "Agent" (case-insensitive, whitespace-tolerant).
      2. Click it. After the click, Flow flips `aria-pressed` to
         `"false"` and the landing screen collapses back into the
         normal composer.
      3. Verify the new state before declaring success.

    Safe and idempotent: if Agent isn't pressed, the function is a
    no-op. If multiple Agent-shaped buttons match, we only toggle
    the *pressed* ones — never touch a depressed (off) pill.
    """
    import re

    toggled = 0
    try:
        # Filter chain: `aria-pressed="true"` first (cheap), then
        # exact-match text "Agent" (case + whitespace insensitive).
        candidates = page.locator("button[aria-pressed='true']").filter(
            has_text=re.compile(r"^\s*Agent\s*$", re.I),
        )
        n = min(candidates.count(), 4)
        for i in range(n):
            try:
                el = candidates.nth(i)
                if not el.is_visible(timeout=300):
                    continue
                logger.info("Flow UI prep: toggling off Agent mode")
                el.click(timeout=1500)
                toggled += 1
                # Brief settle — Flow swaps the composer DOM in
                # response to the click; give it a beat before the
                # next prep step (apply_project_settings) runs.
                page.wait_for_timeout(300)
            except Exception as exc:  # noqa: BLE001
                if is_debug():
                    logger.info(
                        "flow-ui-prep: agent-pill click skipped: %s", exc,
                    )
                continue
    except Exception as exc:  # noqa: BLE001
        if is_debug():
            logger.info("flow-ui-prep: agent-pill sweep error: %s", exc)

    # Verification: confirm no Agent pill is still pressed.
    still_pressed = 0
    try:
        leftover = page.locator("button[aria-pressed='true']").filter(
            has_text=re.compile(r"^\s*Agent\s*$", re.I),
        )
        still_pressed = leftover.count()
    except Exception:  # noqa: BLE001
        pass

    if toggled and still_pressed == 0:
        logger.info("Flow UI prep: Agent mode is now off")
    elif toggled and still_pressed:
        logger.warning(
            "Flow UI prep: clicked Agent pill %d time(s) but "
            "%d still report aria-pressed=true; Flow may have "
            "renamed the pill or wrapped it in a child element.",
            toggled, still_pressed,
        )
    elif is_debug():
        logger.info("Flow UI prep: Agent mode already off")
    return {
        "agent_toggled_off": toggled,
        "agent_still_pressed": still_pressed,
    }


# ---------------------------------------------------------------------
# Step 2 — generation settings
# ---------------------------------------------------------------------

def ensure_image_generation_settings(
    page: "Page",
    *,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
) -> dict:
    """Make sure Flow's composer is in image mode with the canonical
    settings (9:16 aspect, 1x variants, Nano Banana Pro model).

    Wraps the proven `_apply_project_settings` from `recorded_flow`
    — that's the same call site `enter_new_project_if_present`
    uses, just invoked unconditionally per job so we don't drift
    when the user manually changed something between batches.
    """
    if not is_ensure_settings_enabled():
        if is_debug():
            logger.info("flow-ui-prep: ensure-settings disabled by env")
        return {"skipped": True}
    try:
        # Late import — recorded_flow pulls Playwright at import
        # time which we already have at the call site.
        from .recorded_flow import _apply_project_settings  # noqa: WPS450
    except ImportError as exc:
        logger.warning(
            "flow-ui-prep: cannot import _apply_project_settings (%s); "
            "settings verification skipped.", exc,
        )
        return {"skipped": True, "error": str(exc)}
    try:
        _apply_project_settings(
            page, logger=logger, selector_timeout_ms=selector_timeout_ms,
        )
        return {"applied": True}
    except Exception as exc:  # noqa: BLE001
        # Never raise — caller continues to submit anyway. If the
        # settings popover wasn't openable, the per-item recorded
        # flow will hit its own composer-summary check downstream.
        logger.warning(
            "flow-ui-prep: ensure_image_generation_settings non-fatal "
            "error: %s", exc,
        )
        return {"applied": False, "error": str(exc)}


def ensure_video_generation_settings(
    page: "Page",
    *,
    logger: logging.Logger,
) -> dict:
    """Pre-Animate cleanup for the per-tile video flow.

    Only handles stale-overlay cleanup at this point — the actual
    video model selection (Veo 3.1 - Lite, not Omni Flash) happens
    AFTER the Animate menuitem is clicked, because the composer's
    Video tab in the settings popover only renders the right
    options once the favorited tile has been promoted into the
    composer.

    See `ensure_veo_lite_model()` and the call site inside
    `recorded_flow.perform_recorded_video_flow` (right after the
    Animate click, before the prompt is typed).
    """
    if not is_ensure_settings_enabled():
        if is_debug():
            logger.info("flow-ui-prep: ensure-settings (video) disabled by env")
        return {"skipped": True}
    # The existing perform_recorded_video_flow already presses
    # Escape + moves the mouse to (0, 0) at its top. Calling our
    # dismiss helper here adds the aria-label close button + Radix
    # menu sweeps that the recorded flow doesn't do.
    return dismiss_flow_overlays(page, logger=logger)


def ensure_veo_lite_model(
    page: "Page",
    *,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
) -> dict:
    """Pin the video composer's model to "Veo 3.1 - Lite".

    Why this exists: Flow's video composer has a model dropdown
    with options like "Omni Flash" (text-to-video — ignores
    reference images) and "Veo 3.1 - Lite" (image-to-video —
    uses the favorited tile as the source frame). When a session
    lands on Omni Flash, every favorited-image animation gets
    generated without the source image, producing the wrong
    content. The user reported this regression on 2026-06-03.

    Called *after* the Animate menuitem has been clicked (so the
    video composer's Video tab is active in the settings popover)
    but *before* the prompt is typed + submitted. See the call
    site in recorded_flow.perform_recorded_video_flow.

    Best-effort: any failure logs a warning and the video submit
    proceeds with whatever model was previously selected. We
    never raise out of this function.
    """
    if not is_ensure_settings_enabled():
        if is_debug():
            logger.info("flow-ui-prep: veo-lite check disabled by env")
        return {"skipped": True}

    import re

    try:
        from .recorded_flow import _locate_project_settings_trigger
    except ImportError as exc:
        logger.warning(
            "flow-ui-prep: cannot import settings trigger: %s", exc,
        )
        return {"skipped": True, "error": str(exc)}

    # ----- 1. Open the settings popover. -----------------------------
    # The composer pill is what opens it. In video mode the pill
    # reads something like "Video · 4s · 1x"; in image mode it
    # reads "crop_9_16 · 1x". Either way the locator anchors on
    # the `crop_*` ligature that's always present.
    try:
        _locate_project_settings_trigger(page).click(timeout=selector_timeout_ms)
        page.wait_for_timeout(200)
        logger.info("Opened settings popover (video model check)")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "flow-ui-prep: could not open settings popover for video "
            "model check: %s", exc,
        )
        return {"applied": False, "error": str(exc)}

    result: dict = {"applied": True, "model_was": "", "model_now": ""}

    # ----- 2. Make sure the Video tab + Frames sub-tab are active. ---
    # The popover defaults to whatever mode the composer was last in.
    # Frames vs Ingredients is a secondary mode selector inside Video
    # — Frames is what the recorded automation expects.
    try:
        video_tab = page.get_by_role(
            "tab", name=re.compile(r"^\s*video\s*$", re.I),
        ).first
        if video_tab.is_visible(timeout=500):
            selected = (video_tab.get_attribute("aria-selected") or "").lower()
            if selected != "true":
                video_tab.click(timeout=2_000)
                page.wait_for_timeout(150)
                logger.info("Selected Video tab in settings popover")
    except Exception as exc:  # noqa: BLE001
        if is_debug():
            logger.info("flow-ui-prep: video tab select skipped: %s", exc)

    try:
        frames_tab = page.get_by_role(
            "tab", name=re.compile(r"^\s*frames\s*$", re.I),
        ).first
        if frames_tab.is_visible(timeout=500):
            selected = (frames_tab.get_attribute("aria-selected") or "").lower()
            if selected != "true":
                frames_tab.click(timeout=2_000)
                page.wait_for_timeout(150)
                logger.info("Selected Frames sub-tab in settings popover")
    except Exception as exc:  # noqa: BLE001
        if is_debug():
            logger.info("flow-ui-prep: frames sub-tab select skipped: %s", exc)

    # ----- 3. Identify the current model + change if needed. ---------
    # The dropdown is a button-shaped element whose text is the
    # current model name. Try a few selector strategies because
    # Flow has used different DOM shapes for this control over
    # past releases.
    model_trigger = None
    for strat in (
        # Most reliable when Flow uses Radix Select for this.
        lambda: page.get_by_role("combobox").first,
        # Text-based: known model name fragments "Omni Flash" and
        # any "Veo <digit>" pattern.
        lambda: page.locator("button").filter(
            has_text=re.compile(r"(omni\s+flash|veo\s+\d)", re.I),
        ).first,
    ):
        try:
            cand = strat()
            if cand.is_visible(timeout=500):
                model_trigger = cand
                break
        except Exception:  # noqa: BLE001
            continue

    if model_trigger is None:
        logger.warning(
            "flow-ui-prep: could not find video model dropdown in "
            "settings popover; leaving current model untouched.",
        )
        _safe_escape(page)
        result["applied"] = False
        return result

    # Read the current value.
    current = ""
    try:
        current = (model_trigger.inner_text(timeout=500) or "").strip()
    except Exception:  # noqa: BLE001
        pass
    result["model_was"] = current
    if "veo" in current.lower():
        logger.info("Video model already %r — no change", current)
        result["model_now"] = current
        _safe_escape(page)
        return result

    # Open the dropdown.
    try:
        model_trigger.click(timeout=selector_timeout_ms)
        page.wait_for_timeout(300)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "flow-ui-prep: could not open model dropdown (was=%r): %s",
            current, exc,
        )
        _safe_escape(page)
        result["applied"] = False
        result["error"] = str(exc)
        return result

    # Click "Veo 3.1 - Lite". Try role=option first (Radix Select),
    # then menuitem, then plain text. `.first` guards against rare
    # duplicate entries.
    veo_option = None
    for strat in (
        lambda: page.get_by_role(
            "option", name=re.compile(r"veo\s+3\.1.*lite", re.I),
        ).first,
        lambda: page.get_by_role(
            "menuitem", name=re.compile(r"veo\s+3\.1.*lite", re.I),
        ).first,
        lambda: page.get_by_text(
            re.compile(r"^\s*veo\s+3\.1\s*-\s*lite\s*$", re.I),
        ).first,
    ):
        try:
            cand = strat()
            if cand.is_visible(timeout=500):
                veo_option = cand
                break
        except Exception:  # noqa: BLE001
            continue

    if veo_option is None:
        logger.warning(
            "flow-ui-prep: 'Veo 3.1 - Lite' option not visible in the "
            "model dropdown. Flow may have renamed it; current value "
            "%r stays.", current,
        )
        _safe_escape(page)
        result["applied"] = False
        return result

    try:
        veo_option.click(timeout=selector_timeout_ms)
        page.wait_for_timeout(250)
        logger.info(
            "Switched video model: %r → Veo 3.1 - Lite",
            current or "(unknown)",
        )
        result["model_now"] = "Veo 3.1 - Lite"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "flow-ui-prep: clicking 'Veo 3.1 - Lite' failed: %s", exc,
        )
        result["applied"] = False
        result["error"] = str(exc)

    _safe_escape(page)
    return result


def _safe_escape(page: "Page") -> None:
    """Best-effort `keyboard.press('Escape')`. Used to close the
    settings popover after a model-switch attempt; never raises."""
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------
# Top-level: call once per job (image) or per tile (video)
# ---------------------------------------------------------------------

def prepare_flow_for_image_generation(
    page: "Page",
    *,
    logger: logging.Logger,
    selector_timeout_ms: int = 15_000,
) -> dict:
    """One-stop prep before each image submit. Returns a dict the
    caller can include in JobEvent details / diagnostics.

    Step order matters:
      1. Dismiss overlays — get any blocking dialog / menu out of
         the way so subsequent clicks aren't intercepted.
      2. Close agent prompt suggestion *chips* — small in-composer
         pills that intercept the prompt input.
      3. Toggle off *Agent mode* — the composer-toolbar pill that
         routes the Generate arrow through Flow's agent flow
         instead of the standard image-generation flow we automate.
         MUST run before step 4, because the settings popover is
         hidden / behaves differently in Agent mode.
      4. Re-apply 9:16 / 1x / Nano Banana Pro.
    """
    if not is_prep_enabled():
        return {"skipped": True, "reason": "FLOW_UI_PREP_ENABLED=false"}
    report: dict = {}
    report["dismiss"] = dismiss_flow_overlays(page, logger=logger)
    report["agent_pills"] = close_agent_prompt_pills(page, logger=logger)
    report["agent_mode"] = toggle_off_agent_mode(page, logger=logger)
    report["settings"] = ensure_image_generation_settings(
        page, logger=logger, selector_timeout_ms=selector_timeout_ms,
    )
    return report


def prepare_flow_for_video_generation(
    page: "Page",
    *,
    logger: logging.Logger,
) -> dict:
    """One-stop prep before each Animate-from-favorite click.

    Same order as image prep, minus the project-settings re-apply
    (video has no per-job 'mode' to enforce). Agent mode still
    matters — it changes how the tile overflow menu renders.
    """
    if not is_prep_enabled():
        return {"skipped": True, "reason": "FLOW_UI_PREP_ENABLED=false"}
    report: dict = {}
    report["dismiss"] = dismiss_flow_overlays(page, logger=logger)
    report["agent_pills"] = close_agent_prompt_pills(page, logger=logger)
    report["agent_mode"] = toggle_off_agent_mode(page, logger=logger)
    report["settings"] = ensure_video_generation_settings(page, logger=logger)
    return report
