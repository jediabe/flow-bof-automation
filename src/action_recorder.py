"""Record real user actions on a live, authenticated Flow Labs page.

Why this exists:
    `playwright codegen` launches its own browser, and Google blocks
    sign-in there. So we attach to the already-authenticated Chrome over
    CDP, inject a tiny JS listener bundle into the live Flow Labs tab,
    and stream every click / input / paste / file-change back into Python
    along with rich element metadata and candidate Playwright selectors.

The user clicks through the Flow Labs UI normally. We save the captured
event stream as JSON plus a final full-page screenshot; the user then
copies the recorded selectors into `src/recorded_flow.py`.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

from .config import Settings
from .flow_automation import acquire_flow_page, open_flow_browser


# JS that installs document-level capturing listeners and emits each
# event back to Python via the `__flowRecord` binding we expose below.
# Guarded by a flag so re-running the recorder on the same page doesn't
# double-attach.
_RECORDER_JS = r"""
(() => {
  if (window.__flowRecorderInstalled) return;
  window.__flowRecorderInstalled = true;

  const truncate = (s, n) => (s == null ? null : String(s).slice(0, n));
  const escapeDq = (s) => String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');

  function suggestSelectors(el) {
    const out = [];
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    const aria = el.getAttribute('aria-label');
    const placeholder = el.getAttribute('placeholder');
    const id = el.id;
    const dataTestId = el.getAttribute('data-testid');
    const text = (el.innerText || el.textContent || '').trim();
    const shortText = text && text.length <= 60 ? text : null;
    if (dataTestId) out.push(`[data-testid="${escapeDq(dataTestId)}"]`);
    if (aria && role) out.push(`role=${role}[name="${escapeDq(aria)}"]`);
    if (aria) out.push(`[aria-label="${escapeDq(aria)}"]`);
    if (role && shortText && !aria) out.push(`role=${role}[name="${escapeDq(shortText)}"]`);
    if (placeholder) out.push(`${tag}[placeholder="${escapeDq(placeholder)}"]`);
    if (id) {
      try { out.push(`#${CSS.escape(id)}`); } catch (e) { out.push(`#${id}`); }
    }
    if (shortText && ['button','a','div','span','label','li'].includes(tag)) {
      out.push(`${tag}:has-text("${escapeDq(shortText)}")`);
    }
    const implicitRole = {button:'button', a:'link', textarea:'textbox'}[tag];
    if (implicitRole && aria) out.push(`role=${implicitRole}[name="${escapeDq(aria)}"]`);
    if (implicitRole && !aria && shortText) out.push(`role=${implicitRole}[name="${escapeDq(shortText)}"]`);
    return out;
  }

  function elementInfo(el) {
    if (!el || el.nodeType !== 1) return null;
    const rect = el.getBoundingClientRect();
    const cls = (typeof el.className === 'string' && el.className.trim()) ? el.className.trim() : null;
    return {
      tag: el.tagName.toLowerCase(),
      text: truncate((el.innerText || el.textContent || '').trim(), 200),
      aria_label: el.getAttribute('aria-label'),
      placeholder: el.getAttribute('placeholder'),
      role: el.getAttribute('role'),
      id: el.id || null,
      class: cls,
      type: el.getAttribute('type'),
      name: el.getAttribute('name'),
      data_testid: el.getAttribute('data-testid'),
      contenteditable: el.getAttribute('contenteditable'),
      rect: {
        x: Math.round(rect.x), y: Math.round(rect.y),
        w: Math.round(rect.width), h: Math.round(rect.height),
      },
      selectors: suggestSelectors(el),
    };
  }

  function emit(type, el, extra) {
    if (!window.__flowRecord) return;
    const payload = {
      ts: Date.now(),
      type: type,
      element: elementInfo(el),
    };
    if (extra) Object.assign(payload, extra);
    try { window.__flowRecord(payload); } catch (e) {}
  }

  document.addEventListener('click', (e) => emit('click', e.target), true);

  document.addEventListener('input', (e) => {
    const t = e.target;
    if (!t) return;
    const tag = t.tagName ? t.tagName.toLowerCase() : '';
    let value = null;
    if (tag === 'input' || tag === 'textarea') value = t.value;
    else if (t.isContentEditable) value = t.innerText;
    emit('input', t, {
      value: truncate(value, 500),
      value_length: value ? value.length : 0,
    });
  }, true);

  document.addEventListener('paste', (e) => {
    let pasted = '';
    try { pasted = e.clipboardData ? e.clipboardData.getData('text') : ''; } catch (e2) {}
    emit('paste', e.target, {
      pasted: truncate(pasted, 500),
      pasted_length: pasted.length,
    });
  }, true);

  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!t) return;
    if (t.tagName && t.tagName.toLowerCase() === 'input' && t.type === 'file') {
      const files = Array.from(t.files || []).map(f => ({
        name: f.name, size: f.size, type: f.type,
      }));
      emit('file_change', t, { files: files });
    }
  }, true);
})();
"""


def run_record_actions(settings: Settings, logger: logging.Logger) -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = settings.logs_dir / f"action_recording_{timestamp}.json"
    screenshot_path = settings.logs_dir / f"action_recording_{timestamp}.png"

    events: list[dict[str, Any]] = []
    events_lock = threading.Lock()
    starting_url: str | None = None

    def on_event(payload: dict[str, Any]) -> None:
        with events_lock:
            events.append(payload)
        try:
            el = payload.get("element") or {}
            label = (
                el.get("aria_label")
                or el.get("text")
                or el.get("placeholder")
                or el.get("data_testid")
                or ""
            )
            logger.info(
                "[%s] <%s role=%s> %s",
                payload.get("type"),
                el.get("tag", "?"),
                el.get("role") or "-",
                (label or "")[:80],
            )
        except Exception:  # noqa: BLE001
            pass

    stop_event = threading.Event()

    def wait_for_enter() -> None:
        try:
            input()
        except EOFError:
            pass
        stop_event.set()

    with open_flow_browser(settings, logger) as session:
        page = acquire_flow_page(session, settings, logger)
        starting_url = page.url
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

        # Bind the page->Python callback. Tolerate re-binding (already
        # exposed from a previous session in attached Chrome).
        try:
            page.expose_function("__flowRecord", on_event)
        except Exception as exc:  # noqa: BLE001
            logger.info("expose_function skipped (likely already bound): %s", exc)

        # Install listeners on the current document and on any new ones
        # that load (Flow Labs is a SPA but full reloads still happen).
        try:
            page.add_init_script(_RECORDER_JS)
        except Exception:  # noqa: BLE001
            pass
        page.evaluate(_RECORDER_JS)

        print()
        print(f"Recording started on: {page.url}")
        print("Click, type, paste — all your actions on Flow Labs are being logged.")
        print(f"Events will be saved to: {out_path}")
        print()
        print(">>> Press Enter here to STOP recording. <<<")
        print()

        threading.Thread(target=wait_for_enter, daemon=True).start()

        # Polling loop keeps Playwright's event loop turning so exposed
        # callbacks get dispatched onto our `on_event` handler.
        while not stop_event.is_set():
            page.wait_for_timeout(500)

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to save final screenshot")

    with events_lock:
        snapshot = list(events)

    report = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "starting_url": starting_url,
        "event_count": len(snapshot),
        "events": snapshot,
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Recorded {len(snapshot)} events.")
    print(f"JSON:       {out_path}")
    print(f"Screenshot: {screenshot_path}")
    print()
    print("Next: open the JSON, find the events for plus / Add to Prompt /")
    print("prompt input / generate arrow, and paste their `selectors[0]`")
    print("into the matching _locate_* helper in src/recorded_flow.py.")
    return 0
