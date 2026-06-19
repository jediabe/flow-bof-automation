"""Flow UI inspector — comprehensive DOM state dump.

Purpose: when a Flow automation step fails because a selector
mismatches the live DOM, we want one round-trip with the user to
know exactly what changed instead of three rounds of guessing.
This module runs a single JS pass that captures everything we
look at across the runner's automation:

  - Page URL + title + viewport
  - Composer textbox + every button in its scope (text, aria,
    role, rect, class hash, visible flag, has-icon flag)
  - Identified composer elements: +, Agent pill, settings pill,
    submit arrow, prompt textbox — using the same heuristics the
    runner uses, so a mismatch here is exactly the mismatch the
    runner would hit.
  - Settings popover (if open): which tab is selected, aspect
    tabs, variant tabs, current model
  - Overlays / open dialogs / Radix menus
  - Agent assistant panel (resizer + data-state)
  - Gallery tiles (count, first N with media_id + favorited flag)
  - All `google-symbols` Material ligatures by name (so we know
    which icons Flow currently uses — name churn is the most
    common source of selector breakage)

The dump is written as both JSON (machine-readable, full detail)
and a plain-text summary (paste-friendly). Both land in
`<runner_data_dir>/inspections/flow_inspection_<timestamp>.{json,txt}`
— a per-user path that survives an exe upgrade-by-replacement.

Two entry points:

  1. `run_inspector()` — standalone CLI: connects to running
     Chrome over CDP, finds the Flow tab, dumps, writes files,
     returns 0/non-zero. Wired into `runner_app.py` via
     `--inspect-flow` and the interactive menu.

  2. `inspect_and_save_page(page, label)` — when an automation
     handler already holds a Page object, it can call this to
     dump WITHOUT going through CDP. Used by `FLOW_INSPECT_ON_ERROR`
     to auto-capture state on any agent_api exception.

Designed never to raise out of the public functions — diagnostics
must not be the thing that takes down a runner.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


logger = logging.getLogger("flow_inspector")


# ---------------------------------------------------------------------
# Where dumps land
# ---------------------------------------------------------------------

def _inspections_dir() -> Path:
    """Per-user persistent location for inspection dumps.

    Mirrors `runner_app.paths.runner_data_dir()` so the dumps live
    alongside the runner config — the user already knows that path
    from the diagnose output, and it survives PyInstaller
    re-extraction.
    """
    # Late import to avoid circular dep at module load — `runner_app`
    # imports a lot we don't need here.
    try:
        from .runner_app.paths import runner_data_dir, ensure_dir
        return ensure_dir(runner_data_dir() / "inspections")
    except Exception:  # noqa: BLE001
        # Fallback: cwd. Worst case the user finds it next to where
        # they launched the runner.
        fallback = Path.cwd() / "flow_inspections"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


# ---------------------------------------------------------------------
# The JS dump
# ---------------------------------------------------------------------

# One big function so we minimise round-trips and so every section
# of the dump sees the same DOM snapshot. Keep this self-contained:
# no imports, no external state. Returns a JSON-serialisable dict.
_INSPECT_JS = r"""
() => {
  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
           cs.visibility !== 'hidden' &&
           cs.display !== 'none' &&
           parseFloat(cs.opacity || '1') > 0;
  };
  const rect = el => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
    };
  };
  const summarizeBtn = b => ({
    text: (b.innerText || b.textContent || '').slice(0, 80).trim(),
    aria_label: b.getAttribute('aria-label'),
    aria_pressed: b.getAttribute('aria-pressed'),
    aria_selected: b.getAttribute('aria-selected'),
    role: b.getAttribute('role'),
    type: b.getAttribute('type'),
    disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
    visible: isVisible(b),
    rect: rect(b),
    class_name: (b.className || '').toString().slice(0, 80),
    has_icon: !!b.querySelector('i.google-symbols, svg'),
    icon_text: (() => {
      const i = b.querySelector('i.google-symbols');
      return i ? (i.innerText || i.textContent || '').trim() : null;
    })(),
  });

  const out = {
    schema_version: 1,
    page: {
      url: location.href,
      title: document.title,
      viewport: {
        w: window.innerWidth, h: window.innerHeight,
      },
      device_pixel_ratio: window.devicePixelRatio,
    },
    composer: null,
    identified: {
      plus_button: null,
      agent_pill: null,
      settings_pill: null,
      submit_arrow: null,
      prompt_textbox: null,
    },
    settings_popover: null,
    overlays: [],
    agent_assistant_panel: null,
    gallery: null,
    google_symbols: {},
    errors: [],
  };

  // -------- Composer scope --------
  try {
    const textbox = document.querySelector(
      'div[role="textbox"][contenteditable="true"]'
    );
    if (textbox) {
      let scope = textbox;
      for (let i = 0; i < 6 && scope.parentElement; i++) {
        scope = scope.parentElement;
      }
      const buttons = Array.from(scope.querySelectorAll('button'))
        .filter(isVisible);
      out.composer = {
        scope_class: (scope.className || '').toString().slice(0, 80),
        textbox: {
          visible: isVisible(textbox),
          rect: rect(textbox),
          inner_text: (textbox.innerText || '').slice(0, 200),
          placeholder: (() => {
            const ph = textbox.querySelector('[data-slate-placeholder]');
            return ph ? (ph.innerText || ph.textContent || '').trim() : null;
          })(),
          aria_multiline: textbox.getAttribute('aria-multiline'),
        },
        buttons: buttons.map(summarizeBtn),
      };
      out.identified.prompt_textbox = {
        visible: isVisible(textbox),
        rect: rect(textbox),
      };
    } else {
      out.errors.push('no contenteditable textbox found');
    }
  } catch (e) {
    out.errors.push('composer scope failed: ' + e.message);
  }

  // -------- Identified composer elements (using runner's heuristics) --------
  try {
    if (out.composer) {
      const cb = out.composer.buttons;
      const tbRect = out.composer.textbox.rect;
      const sameRow = b => {
        const r = b.rect;
        return !(r.y + r.h < tbRect.y - 20 || r.y > tbRect.y + tbRect.h + 80);
      };
      const rowButtons = cb.filter(sameRow);

      // + button: leftmost on the row that isn't a known non-target.
      const isRejected = b => {
        const text = (b.text || '').toLowerCase();
        const aria = (b.aria_label || '').toLowerCase();
        if (text === 'agent' || aria === 'agent') return 'agent';
        if (aria.includes('send') || text.includes('arrow_forward')) return 'send';
        if (text.includes('crop_') || aria.includes('settings')) return 'settings_pill';
        if (text.includes('nano banana') || text.includes('veo')) return 'model_name';
        if (/^\s*[1-9]x\s*$/.test(text)) return 'variant_tab';
        return null;
      };
      const plusCandidates = rowButtons.filter(b => !isRejected(b));
      plusCandidates.sort((a, b) => a.rect.x - b.rect.x);
      if (plusCandidates.length > 0) {
        out.identified.plus_button = {
          ...plusCandidates[0],
          chosen_from: plusCandidates.length,
          row_total: rowButtons.length,
        };
      }

      // Agent pill: aria-pressed AND text === 'Agent'.
      const agentBtn = cb.find(b =>
        (b.aria_pressed !== null) &&
        /^\s*Agent\s*$/i.test(b.text || '')
      );
      if (agentBtn) out.identified.agent_pill = agentBtn;

      // Settings pill: contains a model-name token.
      const modelRe = /(nano\s+banana|veo\s+\d|gemini|imagen|omni\s+flash|wan|seedance)/i;
      const settingsBtn = cb.find(b => modelRe.test(b.text || ''));
      if (settingsBtn) out.identified.settings_pill = settingsBtn;

      // Submit arrow: contains 'arrow_forward' text.
      const submitBtn = cb.find(b => /arrow_forward/i.test(b.text || ''));
      if (submitBtn) out.identified.submit_arrow = submitBtn;
    }
  } catch (e) {
    out.errors.push('identification failed: ' + e.message);
  }

  // -------- Settings popover --------
  try {
    const popoverSelectors = [
      '[role="dialog"]',
      '[data-radix-popper-content-wrapper]',
      '[data-state="open"][data-radix-popper-content]',
    ];
    let popover = null;
    for (const sel of popoverSelectors) {
      const els = Array.from(document.querySelectorAll(sel));
      const visible = els.find(isVisible);
      if (visible) { popover = visible; break; }
    }
    if (popover) {
      const tabs = Array.from(popover.querySelectorAll('[role="tab"]'))
        .filter(isVisible).map(t => ({
          name: (t.innerText || t.textContent || '').slice(0, 30).trim(),
          aria_selected: t.getAttribute('aria-selected'),
          rect: rect(t),
        }));
      out.settings_popover = {
        present: true,
        rect: rect(popover),
        tabs,
        text_snippet: (popover.innerText || '').slice(0, 400),
      };
    } else {
      out.settings_popover = {present: false};
    }
  } catch (e) {
    out.errors.push('settings popover scan failed: ' + e.message);
  }

  // -------- Overlays + open dialogs / menus --------
  try {
    const overlaySels = [
      '[role="dialog"]', '[data-radix-menu-content]', '[role="menu"]',
      '[role="alertdialog"]', '[data-state="open"][data-radix-popper-content]',
    ];
    const seen = new Set();
    for (const sel of overlaySels) {
      const els = Array.from(document.querySelectorAll(sel))
        .filter(isVisible);
      for (const el of els) {
        if (seen.has(el)) continue;
        seen.add(el);
        out.overlays.push({
          selector_hit: sel,
          tag: el.tagName.toLowerCase(),
          rect: rect(el),
          text_snippet: (el.innerText || '').slice(0, 120).trim(),
        });
      }
    }
  } catch (e) {
    out.errors.push('overlays scan failed: ' + e.message);
  }

  // -------- Agent assistant panel --------
  try {
    const resizer = document.querySelector(
      '[role="separator"][aria-label="Resize agent panel"]'
    );
    if (resizer) {
      let panel = resizer;
      for (let i = 0; i < 4 && panel.parentElement; i++) {
        panel = panel.parentElement;
        const ds = panel.getAttribute('data-state');
        if (ds) break;
      }
      out.agent_assistant_panel = {
        resizer_visible: isVisible(resizer),
        panel_data_state: panel ? panel.getAttribute('data-state') : null,
        panel_class: panel ? (panel.className || '').toString().slice(0, 80) : null,
        panel_rect: panel ? rect(panel) : null,
      };
    } else {
      out.agent_assistant_panel = {resizer_visible: false};
    }
  } catch (e) {
    out.errors.push('agent panel scan failed: ' + e.message);
  }

  // -------- Gallery tiles --------
  try {
    const tiles = Array.from(document.querySelectorAll('[data-tile-id]'));
    const visibleTiles = tiles.filter(isVisible);
    const firstN = visibleTiles.slice(0, 12).map(t => {
      const img = t.querySelector('img[src*="media.getMediaUrlRedirect"], img[src*="/fx/api/trpc/media"]');
      let media_id = null;
      if (img) {
        const m = (img.src || '').match(/name=([^&]+)/);
        if (m) media_id = decodeURIComponent(m[1]);
      }
      // Look for any favorite indicator inside the tile.
      const heart = t.querySelector('[aria-label*="favorite" i], [aria-label*="liked" i], button:has(i.google-symbols):not([aria-label])');
      const favorited = (() => {
        // Heuristic: a filled-heart icon, or aria-pressed on a favorite button.
        const buttons = t.querySelectorAll('button');
        for (const b of buttons) {
          const aria = (b.getAttribute('aria-label') || '').toLowerCase();
          if (aria.includes('favorite') || aria.includes('liked')) {
            return b.getAttribute('aria-pressed') === 'true';
          }
        }
        return null;
      })();
      // Look for any "submitted" / "previously animated" badge inside the tile.
      const badgeText = (t.innerText || '').toLowerCase();
      const has_submitted_badge =
        badgeText.includes('submitted') ||
        badgeText.includes('animated') ||
        badgeText.includes('already');
      return {
        tile_id: t.getAttribute('data-tile-id'),
        media_id,
        favorited,
        has_submitted_badge,
        rect: rect(t),
        visible: isVisible(t),
      };
    });
    out.gallery = {
      total_tiles: tiles.length,
      visible_tiles: visibleTiles.length,
      sample: firstN,
    };
  } catch (e) {
    out.errors.push('gallery scan failed: ' + e.message);
  }

  // -------- Material ligature inventory --------
  try {
    const icons = Array.from(document.querySelectorAll('i.google-symbols'))
      .filter(isVisible);
    const byName = {};
    for (const i of icons) {
      const name = (i.innerText || i.textContent || '').trim();
      if (!name) continue;
      byName[name] = (byName[name] || 0) + 1;
    }
    out.google_symbols = byName;
  } catch (e) {
    out.errors.push('icon inventory failed: ' + e.message);
  }

  return out;
}
"""


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def inspect_flow_state(page: Page) -> dict:
    """Run the DOM dump and return as a dict.

    Never raises — exceptions are stuffed into the `errors` field
    so a partially-broken page still yields useful output.
    """
    out: dict = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "errors": [],
    }
    try:
        page_state = page.evaluate(_INSPECT_JS)
        out.update(page_state)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"evaluate failed: {exc}")
        out["traceback"] = traceback.format_exc()
    return out


def write_inspection(
    state: dict,
    *,
    outputs_dir: Optional[Path] = None,
    label: str = "",
) -> tuple[Path, Path]:
    """Write the inspection as a JSON + human-readable text file.

    Returns (json_path, txt_path). The directory is created if
    needed. Filenames embed the timestamp + the optional `label`
    so multiple captures from one session don't collide.
    """
    out_dir = outputs_dir or _inspections_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:40] if label else "manual"
    base = f"flow_inspection_{ts}_{safe}"

    json_path = out_dir / f"{base}.json"
    txt_path = out_dir / f"{base}.txt"

    try:
        json_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("inspection JSON write failed: %s", exc)

    try:
        txt_path.write_text(_format_summary(state), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("inspection summary write failed: %s", exc)

    return json_path, txt_path


def inspect_and_save_page(
    page: Page,
    *,
    label: str = "",
    outputs_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Run the inspector + write to disk in one call. Returns the
    JSON path on success, None on failure.

    Designed for use inside automation handlers: when a step
    fails, the caller can call this to capture state for triage
    without having to thread a separate CDP connection.
    """
    try:
        state = inspect_flow_state(page)
        json_path, txt_path = write_inspection(
            state, outputs_dir=outputs_dir, label=label,
        )
        logger.info(
            "Flow UI inspection saved:\n  %s\n  %s",
            json_path, txt_path,
        )
        return json_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("inspect_and_save_page failed: %s", exc)
        return None


# ---------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------

def _format_summary(state: dict) -> str:
    """Plain-text dump of the most-useful fields. Optimised for
    'paste into a Slack message' — keeps the JSON nearby on disk
    for full detail when needed."""
    lines: list[str] = []
    w = lines.append

    w("Flow UI inspection")
    w("=" * 60)
    w(f"captured_at: {state.get('captured_at')}")
    page = state.get("page") or {}
    w(f"url:         {page.get('url')}")
    w(f"title:       {page.get('title')}")
    vp = page.get("viewport") or {}
    w(f"viewport:    {vp.get('w')}x{vp.get('h')} (dpr={page.get('device_pixel_ratio')})")
    w("")

    # Identified composer elements — these are what the runner
    # selectors look for. If anything here is missing, that's the
    # bug we need to chase.
    w("Identified composer elements")
    w("-" * 60)
    for key in ("plus_button", "agent_pill", "settings_pill",
                "submit_arrow", "prompt_textbox"):
        el = (state.get("identified") or {}).get(key)
        if not el:
            w(f"  {key:<16} MISSING")
            continue
        text = (el.get("text") or "").replace("\n", " ⏎ ")[:60]
        w(f"  {key:<16} text={text!r}")
        if el.get("aria_label"):
            w(f"  {'':<16}   aria={el['aria_label']!r}")
        if el.get("aria_pressed") is not None:
            w(f"  {'':<16}   aria-pressed={el['aria_pressed']}")
        if el.get("rect"):
            w(f"  {'':<16}   rect={el['rect']}")
    w("")

    # Composer-row buttons (full list).
    comp = state.get("composer") or {}
    if comp:
        buttons = comp.get("buttons") or []
        w(f"Composer buttons ({len(buttons)})")
        w("-" * 60)
        for i, b in enumerate(buttons):
            text = (b.get("text") or "").replace("\n", " ⏎ ")[:50]
            aria = b.get("aria_label") or ""
            w(f"  [{i:>2}] text={text!r:<54} aria={aria!r}")
            extra = []
            if b.get("aria_pressed") is not None:
                extra.append(f"pressed={b['aria_pressed']}")
            if b.get("disabled"):
                extra.append("disabled")
            if b.get("icon_text"):
                extra.append(f"icon={b['icon_text']!r}")
            if extra:
                w(f"        {' / '.join(extra)}")
            if b.get("rect"):
                w(f"        rect={b['rect']}")
        w("")

    # Settings popover.
    pop = state.get("settings_popover") or {}
    if pop.get("present"):
        w("Settings popover  PRESENT")
        for t in (pop.get("tabs") or []):
            sel = t.get("aria_selected")
            marker = "*" if sel == "true" else " "
            w(f"  {marker} tab name={t.get('name')!r:<24} aria-selected={sel}")
    else:
        w("Settings popover  not present (closed)")
    w("")

    # Agent panel.
    ap = state.get("agent_assistant_panel") or {}
    if ap.get("resizer_visible"):
        w(f"Agent assistant panel  OPEN  data-state={ap.get('panel_data_state')!r}")
        w(f"  rect={ap.get('panel_rect')}")
    else:
        w("Agent assistant panel  closed")
    w("")

    # Overlays.
    overlays = state.get("overlays") or []
    w(f"Overlays / open menus ({len(overlays)})")
    if overlays:
        for o in overlays:
            snip = (o.get("text_snippet") or "").replace("\n", " ⏎ ")[:80]
            w(f"  {o.get('selector_hit'):<40} text={snip!r}")
    w("")

    # Gallery.
    gallery = state.get("gallery") or {}
    if gallery:
        w(f"Gallery  total={gallery.get('total_tiles')}  visible={gallery.get('visible_tiles')}")
        sample = gallery.get("sample") or []
        for t in sample[:12]:
            badges = []
            if t.get("favorited") is True:
                badges.append("★FAV")
            if t.get("has_submitted_badge"):
                badges.append("SUBMITTED")
            badge_str = (" " + " ".join(badges)) if badges else ""
            mid = (t.get("media_id") or "")[:12]
            w(f"  media={mid:<14} tile={t.get('tile_id')!r:<40}{badge_str}")
    w("")

    # Material ligatures.
    symbols = state.get("google_symbols") or {}
    if symbols:
        w(f"Material ligatures in use ({len(symbols)} distinct)")
        for name, count in sorted(symbols.items()):
            w(f"  {name!r:<32} ×{count}")
    w("")

    errs = state.get("errors") or []
    if errs:
        w("Errors during inspection")
        w("-" * 60)
        for e in errs:
            w(f"  {e}")
    return "\n".join(lines)


# ---------------------------------------------------------------------
# CLI: connect to running Chrome, find Flow tab, dump
# ---------------------------------------------------------------------

def run_inspector(
    *,
    chrome_port: int = 9222,
    outputs_dir: Optional[Path] = None,
) -> int:
    """Standalone CLI entry: connect to the runner's Chrome and
    dump the first Flow tab found.

    Returns 0 on success, non-zero on failure. Prints the dump
    paths so the user can find them.
    """
    cdp_url = f"http://127.0.0.1:{chrome_port}"
    print(f"Connecting to Chrome over CDP at {cdp_url}...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=8_000)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] Could not attach to Chrome at {cdp_url}: {exc}")
            print(
                "       Make sure the runner's dedicated Chrome window is "
                "open (Open/Reopen Google Flow browser from the menu)."
            )
            return 2

        # Find the Flow tab. The runner only opens one context, but
        # there could be multiple pages inside it.
        contexts = browser.contexts
        if not contexts:
            print("[FAIL] No browser context attached.")
            return 3

        page = _find_flow_page(contexts)
        if page is None:
            print("[FAIL] No Flow page found across attached contexts.")
            print("       Open https://labs.google/fx/tools/flow in the runner's Chrome.")
            return 4

        print(f"Found Flow page: {page.url}")
        state = inspect_flow_state(page)
        json_path, txt_path = write_inspection(
            state, outputs_dir=outputs_dir, label="manual",
        )
        print()
        print("Flow UI inspection saved:")
        print(f"  JSON: {json_path}")
        print(f"  Text: {txt_path}")
        print()
        print("Open the .txt for a paste-friendly summary, or attach the")
        print(".json to a bug report for full DOM detail.")
        return 0


def _find_flow_page(contexts) -> Optional[Page]:
    """Return the first page that looks like Google Flow. Falls
    back to any page if none match the Flow URL — useful when the
    page hasn't navigated yet."""
    for ctx in contexts:
        for pg in ctx.pages:
            url = (pg.url or "").lower()
            if "labs.google" in url and "flow" in url:
                return pg
    # Fallback: any non-blank page.
    for ctx in contexts:
        for pg in ctx.pages:
            if pg.url and pg.url != "about:blank":
                return pg
    return None


# ---------------------------------------------------------------------
# Env-var triggered on-error auto-capture
# ---------------------------------------------------------------------

def is_inspect_on_error_enabled() -> bool:
    """Whether agent_api should auto-capture state on exception.

    Env: FLOW_INSPECT_ON_ERROR=true|1|yes (default: false).
    """
    raw = (os.environ.get("FLOW_INSPECT_ON_ERROR") or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}
