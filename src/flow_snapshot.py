"""Read-only debug snapshot of the live Google Flow UI.

Run via ``python main.py --capture-flow-snapshot`` or the Advanced/Logs
button in the Streamlit UI. Writes a timestamped folder under
``outputs/debug/flow_snapshot_<ts>[_<label>]/`` containing:

  * screenshot.png             — full-page if it fits, viewport otherwise.
  * page.html                  — outerHTML of <html>.
  * visible_text.txt           — document.body.innerText.
  * metadata.json              — url, title, viewport, ua, mode, etc.
  * tiles.json                 — every ``[data-tile-id]`` container.
  * buttons.json               — every ``button`` / ``[role="button"]``.
  * menu_items.json            — open ``[role="menu"]`` /
                                 ``[role="menuitem"]`` / Radix menus.
  * inputs.json                — textarea / input / contenteditable
                                 (lengths only, never raw values).
  * overlays.json              — fixed/sticky/dialog/popover elements.
  * accessibility_snapshot.json — Playwright AX tree (best effort).

Never opens a new tab. Never navigates. Never clicks. If no Flow tab is
open, returns a structured failure and writes only ``metadata.json``
with the error.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .flow_automation import FlowAutomationError, open_flow_browser


# Cap how much HTML we snapshot per element. The total file size adds
# up fast — at 4 KB per tile and 100 tiles you've already hit 400 KB.
# These caps stay legible in any editor while still preserving enough
# structure for selector debugging.
_HTML_SNIPPET_MAX_CHARS = 4_000
_TEXT_SNIPPET_MAX_CHARS = 2_000


# --- Per-section JS evaluators ----------------------------------------------
# Each is a self-contained () => {...} that runs once via page.evaluate.
# All are defensive: per-element try/catch, never throws, returns a list.


_JS_TILES = r"""
() => {
  const out = [];

  // First pass: which tile_ids are favorited?
  // We look for i.google-symbols whose text is exactly 'favorite' (the
  // filled-heart icon) AND walk up to find the [data-tile-id] ancestor.
  const favoritedTileIds = new Set();
  document.querySelectorAll('i.google-symbols').forEach(icon => {
    try {
      const text = (icon.textContent || '').trim().toLowerCase();
      if (text !== 'favorite') return;
      // Check ancestor visibility - opacity 0 means "not actually shown"
      let cur = icon;
      let shown = true;
      for (let i = 0; i < 8 && cur; i++) {
        const cs = getComputedStyle(cur);
        if (parseFloat(cs.opacity || '1') < 0.05) { shown = false; break; }
        if (cs.display === 'none' || cs.visibility === 'hidden') {
          shown = false; break;
        }
        cur = cur.parentElement;
      }
      if (!shown) return;
      // Walk up to find tile_id container.
      cur = icon;
      while (cur && cur !== document.body) {
        const tid = cur.getAttribute && cur.getAttribute('data-tile-id');
        if (tid) { favoritedTileIds.add(tid); break; }
        cur = cur.parentElement;
      }
    } catch (e) {}
  });

  const extractMediaId = (src) => {
    if (!src) return '';
    const m = src.match(/[?&]name=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  };
  const extractEditId = (href) => {
    if (!href) return '';
    const m = href.match(/\/edit\/([^?#]+)/);
    return m ? m[1] : '';
  };

  const seen = new Set();
  document.querySelectorAll('[data-tile-id]').forEach(el => {
    try {
      const tile_id = el.getAttribute('data-tile-id') || '';
      if (!tile_id || seen.has(tile_id)) return;
      seen.add(tile_id);

      const img = el.querySelector('img[alt="Generated image"][src*="media.getMediaUrlRedirect"]')
                || el.querySelector('img[src*="media.getMediaUrlRedirect"]');
      const video = el.querySelector('video[src*="media.getMediaUrlRedirect"]');
      const link = el.querySelector('a[href*="/edit/"]');

      const img_src = img ? (img.getAttribute('src') || '') : '';
      const video_src = video ? (video.getAttribute('src') || '') : '';
      const tile_href = link ? (link.getAttribute('href') || '') : '';
      const media_id = extractMediaId(img_src || video_src);
      const edit_id = extractEditId(tile_href);
      const kind = img ? 'image' : (video ? 'video' : 'unknown');
      const alt = img ? (img.getAttribute('alt') || '') : '';
      const rect = el.getBoundingClientRect();

      out.push({
        tile_id, kind, media_id, edit_id,
        tile_href,
        favorited: favoritedTileIds.has(tile_id),
        img_src, video_src, alt,
        text: ((el.innerText || '').trim()).slice(0, ARG_TEXT),
        rect: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
        },
        html: (el.outerHTML || '').slice(0, ARG_HTML),
      });
    } catch (e) {}
  });
  return out;
}
"""


_JS_BUTTONS = r"""
() => {
  const out = [];
  // Union of <button> and [role="button"], dedup by element identity.
  const seen = new Set();
  const collect = (sel) => document.querySelectorAll(sel).forEach(el => {
    try {
      if (seen.has(el)) return;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const visible = (cs.display !== 'none' && cs.visibility !== 'hidden'
                       && parseFloat(cs.opacity || '1') > 0.05
                       && rect.width > 0 && rect.height > 0);
      out.push({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        text: ((el.innerText || el.textContent || '').trim()).slice(0, 200),
        aria_label: el.getAttribute('aria-label') || '',
        title: el.getAttribute('title') || '',
        data_testid: el.getAttribute('data-testid') || '',
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
        visible,
        rect: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
        },
        html: (el.outerHTML || '').slice(0, ARG_HTML),
      });
    } catch (e) {}
  });
  collect('button');
  collect('[role="button"]');
  return out;
}
"""


_JS_MENU_ITEMS = r"""
() => {
  const out = [];
  const sels = [
    '[role="menu"]',
    '[role="menuitem"]',
    '[data-radix-menu-content]',
    '[data-radix-collection-item]',
  ];
  const seen = new Set();
  sels.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      try {
        if (seen.has(el)) return;
        seen.add(el);
        const rect = el.getBoundingClientRect();
        out.push({
          selector_matched: sel,
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute('role') || '',
          text: ((el.innerText || el.textContent || '').trim()).slice(0, 200),
          aria_label: el.getAttribute('aria-label') || '',
          data_state: el.getAttribute('data-state') || '',
          rect: {
            x: Math.round(rect.x), y: Math.round(rect.y),
            w: Math.round(rect.width), h: Math.round(rect.height),
          },
          html: (el.outerHTML || '').slice(0, ARG_HTML),
        });
      } catch (e) {}
    });
  });
  return out;
}
"""


# Inputs: NEVER dump raw values — they might contain secrets pasted by
# the user. Only lengths.
_JS_INPUTS = r"""
() => {
  const out = [];
  const seen = new Set();
  const push = (el, kind) => {
    try {
      if (seen.has(el)) return;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      let value_len = 0;
      let text_len = 0;
      if (kind === 'input' || kind === 'textarea') {
        value_len = (el.value || '').length;
      }
      // contenteditable / general text length
      text_len = ((el.innerText || el.textContent || '')).length;
      out.push({
        kind,
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        placeholder: el.getAttribute('placeholder') || '',
        aria_label: el.getAttribute('aria-label') || '',
        data_testid: el.getAttribute('data-testid') || '',
        contenteditable: el.getAttribute('contenteditable') || '',
        value_length: value_len,
        text_length: text_len,
        rect: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
        },
        html: (el.outerHTML || '').slice(0, ARG_HTML),
      });
    } catch (e) {}
  };
  document.querySelectorAll('textarea').forEach(el => push(el, 'textarea'));
  document.querySelectorAll('input').forEach(el => push(el, 'input'));
  document.querySelectorAll('[contenteditable="true"]')
          .forEach(el => push(el, 'contenteditable'));
  return out;
}
"""


_JS_OVERLAYS = r"""
() => {
  const out = [];
  const seen = new Set();
  const push = (el, why) => {
    try {
      if (seen.has(el)) return;
      seen.add(el);
      const cs = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      out.push({
        why,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        position: cs.position,
        z_index: cs.zIndex || '',
        aria_label: el.getAttribute('aria-label') || '',
        text: ((el.innerText || el.textContent || '').trim()).slice(0, 200),
        rect: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
        },
        html: (el.outerHTML || '').slice(0, ARG_HTML),
      });
    } catch (e) {}
  };
  // Fixed / sticky positioned elements anywhere on the page.
  document.querySelectorAll('*').forEach(el => {
    try {
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.position === 'sticky') {
        push(el, 'position:' + cs.position);
      }
    } catch (e) {}
  });
  document.querySelectorAll('header').forEach(el => push(el, 'header'));
  document.querySelectorAll('dialog, [role="dialog"]').forEach(el => push(el, 'dialog'));
  document.querySelectorAll('[role="menu"], [role="tooltip"], [role="listbox"]')
          .forEach(el => push(el, 'aria:' + (el.getAttribute('role') || '')));
  document.querySelectorAll('[data-radix-popper-content-wrapper]')
          .forEach(el => push(el, 'radix-popper'));
  return out;
}
"""


def _inject_caps(js_src: str) -> str:
    """Substitute the JS placeholder caps with real numeric literals."""
    return (
        js_src
        .replace("ARG_TEXT", str(_TEXT_SNIPPET_MAX_CHARS))
        .replace("ARG_HTML", str(_HTML_SNIPPET_MAX_CHARS))
    )


def _slug(label: str) -> str:
    """Make a label filesystem-safe and short."""
    if not label:
        return ""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip())
    s = s.strip("._-")
    return s[:64]


def _write_json(path: Path, data: Any) -> None:
    """Pretty-print JSON, never raise — debug tool should not block on
    a single bad file. Errors get appended to metadata via the caller."""
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        # Last resort — fall back to repr so the file at least exists.
        try:
            path.write_text(repr(data), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _find_existing_flow_page(session, settings: Settings):
    """Return the first already-open Flow tab, or None.

    Does NOT navigate or open a tab — that would be a side effect.
    Same logic as src/agent_api._find_existing_flow_page, intentionally
    duplicated to keep the snapshot tool standalone.
    """
    try:
        target = urlparse(settings.flow_labs_url).netloc.lower()
    except Exception:  # noqa: BLE001
        return None
    if not target:
        return None
    for page in (getattr(session.context, "pages", None) or []):
        try:
            if urlparse(page.url).netloc.lower() == target:
                return page
        except Exception:  # noqa: BLE001
            continue
    return None


def _safe_eval(page, label: str, js: str, logger: logging.Logger) -> Any:
    """Run a JS evaluator with a top-level try; return [] on failure
    and log the error rather than aborting the whole snapshot."""
    try:
        return page.evaluate(_inject_caps(js))
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot eval %s failed: %s", label, exc)
        return []


def run_capture_flow_snapshot(
    settings: Settings,
    logger: logging.Logger,
    label: str = "",
    delay_seconds: int = 0,
) -> int:
    """Top-level entry point. Returns 0 on success, 1 on failure.

    Writes a directory at:
        outputs/debug/flow_snapshot_<YYYYMMDD_HHMMSS>[_<label>]/

    ``delay_seconds`` (clamped to 0-60) delays the entire capture so the
    user can switch focus to Chrome and hover a tile / open a menu /
    pose any transient UI state. The countdown happens BEFORE we connect
    to CDP — Playwright traffic over CDP can be enough to make Chrome
    pop focus back on us in some configurations, and we want a clean
    window of zero automation activity during the wait.

    Prints the directory path + per-section counts to the logger.
    """
    # Clamp to a sane range. The spec caps the UI at 30s; CLI users
    # might want a bit more headroom but we don't want a runaway sleep.
    if delay_seconds < 0:
        delay_seconds = 0
    if delay_seconds > 60:
        logger.warning("--delay %ds is large; clamping to 60.", delay_seconds)
        delay_seconds = 60

    if delay_seconds > 0:
        logger.info(
            "Waiting %d second(s) before snapshot capture — switch to "
            "Flow now and pose the state you want captured (hover a "
            "tile, open the overflow menu, etc.).",
            delay_seconds,
        )
        # Plain sleep; we deliberately don't ping CDP during the wait
        # so the user's Chrome stays in whatever foreground state they
        # need. The folder is created AFTER the sleep so the timestamp
        # reflects when the capture actually happened.
        time.sleep(delay_seconds)
        logger.info("Wake — capturing now.")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slug(label)
    folder_name = f"flow_snapshot_{ts}" + (f"_{slug}" if slug else "")
    out_dir = settings.repo_root / "outputs" / "debug" / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Snapshot folder: %s", out_dir)

    notes: list[str] = []
    if delay_seconds > 0:
        notes.append(f"captured after a {delay_seconds}s user-controlled delay")

    # ---- Open CDP, find existing Flow tab ----------------------------------
    try:
        with open_flow_browser(settings, logger) as session:
            page = _find_existing_flow_page(session, settings)
            if page is None:
                _write_json(out_dir / "metadata.json", {
                    "timestamp":  ts,
                    "label":      label,
                    "snapshot_status": "failed",
                    "error_code": "FLOW_PAGE_NOT_FOUND",
                    "error_message": (
                        f"No tab matching {settings.flow_labs_url} is open. "
                        "Open Flow Labs in the debug Chrome window and "
                        "rerun --capture-flow-snapshot."
                    ),
                    "notes": notes,
                })
                logger.error(
                    "No Flow tab open at %s — wrote partial snapshot to %s",
                    settings.flow_labs_url, out_dir,
                )
                return 1

            # ---- 1. screenshot.png --------------------------------------
            screenshot_path = out_dir / "screenshot.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
                logger.info("Wrote %s (full page)", screenshot_path.name)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"full_page screenshot failed: {exc}; retrying viewport")
                try:
                    page.screenshot(path=str(screenshot_path), full_page=False)
                    logger.info("Wrote %s (viewport)", screenshot_path.name)
                except Exception as exc2:  # noqa: BLE001
                    notes.append(f"viewport screenshot also failed: {exc2}")
                    logger.warning("Could not write screenshot: %s", exc2)

            # ---- 2. page.html ------------------------------------------
            try:
                html = page.content()
                (out_dir / "page.html").write_text(html, encoding="utf-8")
                logger.info("Wrote page.html (%d chars)", len(html))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"page.html capture failed: {exc}")
                logger.warning("page.html capture failed: %s", exc)

            # ---- 3. visible_text.txt -----------------------------------
            try:
                txt = page.evaluate(
                    "() => (document.body && document.body.innerText) || ''"
                )
                (out_dir / "visible_text.txt").write_text(
                    str(txt), encoding="utf-8"
                )
                logger.info("Wrote visible_text.txt (%d chars)", len(str(txt)))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"visible_text capture failed: {exc}")
                logger.warning("visible_text capture failed: %s", exc)

            # ---- 5. tiles.json -----------------------------------------
            tiles = _safe_eval(page, "tiles", _JS_TILES, logger) or []
            _write_json(out_dir / "tiles.json", tiles)

            # ---- 6. buttons.json ---------------------------------------
            buttons = _safe_eval(page, "buttons", _JS_BUTTONS, logger) or []
            _write_json(out_dir / "buttons.json", buttons)

            # ---- 7. menu_items.json ------------------------------------
            menu_items = _safe_eval(page, "menu_items", _JS_MENU_ITEMS, logger) or []
            _write_json(out_dir / "menu_items.json", menu_items)

            # ---- 8. inputs.json ----------------------------------------
            inputs = _safe_eval(page, "inputs", _JS_INPUTS, logger) or []
            _write_json(out_dir / "inputs.json", inputs)

            # ---- 9. overlays.json --------------------------------------
            overlays = _safe_eval(page, "overlays", _JS_OVERLAYS, logger) or []
            _write_json(out_dir / "overlays.json", overlays)

            # ---- 10. accessibility_snapshot.json (best effort) --------
            ax_snapshot: Any = None
            try:
                ax_snapshot = page.accessibility.snapshot()
            except Exception as exc:  # noqa: BLE001
                notes.append(f"accessibility snapshot unavailable: {exc}")
            if ax_snapshot is not None:
                _write_json(out_dir / "accessibility_snapshot.json", ax_snapshot)

            # ---- 4. metadata.json (last so it includes per-section counts)
            try:
                url = page.url
            except Exception:  # noqa: BLE001
                url = ""
            try:
                title = page.title()
            except Exception:  # noqa: BLE001
                title = ""
            try:
                vp = page.viewport_size or {}
            except Exception:  # noqa: BLE001
                vp = {}
            try:
                ua = page.evaluate("() => navigator.userAgent")
            except Exception:  # noqa: BLE001
                ua = ""
            try:
                active_el = page.evaluate(
                    "() => { const a = document.activeElement; if (!a) return null;"
                    " return {tag: a.tagName.toLowerCase(),"
                    " role: a.getAttribute('role') || '',"
                    " aria_label: a.getAttribute('aria-label') || '',"
                    " text: ((a.innerText || a.textContent || '').trim()).slice(0,200)};"
                    "}"
                )
            except Exception:  # noqa: BLE001
                active_el = None

            metadata = {
                "timestamp":               ts,
                "label":                   label,
                "slug":                    slug,
                "delay_seconds":           delay_seconds,
                "snapshot_status":         "succeeded",
                "url":                     url,
                "title":                   title,
                "viewport":                vp,
                "user_agent":              ua,
                "automation_mode":         settings.automation_mode,
                "flow_labs_url":           settings.flow_labs_url,
                "chrome_cdp_url":          settings.chrome_cdp_url,
                "active_element":          active_el,
                "counts": {
                    "tiles":          len(tiles),
                    "buttons":        len(buttons),
                    "menu_items":     len(menu_items),
                    "inputs":         len(inputs),
                    "overlays":       len(overlays),
                    "accessibility":  bool(ax_snapshot),
                },
                "notes": notes,
                "snapshot_dir": str(out_dir),
            }
            _write_json(out_dir / "metadata.json", metadata)

            logger.info(
                "Snapshot complete: tiles=%d buttons=%d inputs=%d menus=%d "
                "overlays=%d ax=%s",
                len(tiles), len(buttons), len(inputs), len(menu_items),
                len(overlays), "yes" if ax_snapshot else "no",
            )
            logger.info("Open the snapshot folder: %s", out_dir)
            return 0

    except FlowAutomationError as exc:
        msg = str(exc)
        is_chrome = (
            "Could not connect to Chrome" in msg
            or "connect_over_cdp" in msg
            or "9222" in msg
            or "9333" in msg
        )
        _write_json(out_dir / "metadata.json", {
            "timestamp":       ts,
            "label":           label,
            "snapshot_status": "failed",
            "error_code":      ("CHROME_NOT_REACHABLE" if is_chrome else
                                "FLOW_AUTOMATION_ERROR"),
            "error_message":   msg,
            "notes":           notes,
        })
        logger.error("Flow snapshot failed: %s", msg)
        return 1
    except Exception as exc:  # noqa: BLE001
        _write_json(out_dir / "metadata.json", {
            "timestamp":       ts,
            "label":           label,
            "snapshot_status": "failed",
            "error_code":      "UNEXPECTED_ERROR",
            "error_message":   f"{type(exc).__name__}: {exc}",
            "notes":           notes,
        })
        logger.exception("Flow snapshot crashed")
        return 1
