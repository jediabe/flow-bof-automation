"""Inspect a live Flow Labs page and dump candidate selectors to JSON.

This module is read-only — it does not click, type, or navigate beyond
reusing whatever page is already open.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from .config import Settings
from .flow_automation import acquire_flow_page, open_flow_browser


# JavaScript that pulls every interactive-looking element off the page and
# returns plain dicts. Runs in the page context, so no Python imports here.
_HARVEST_JS = r"""
() => {
  const selectors = [
    'button',
    'input',
    'textarea',
    'select',
    '[role="button"]',
    '[role="textbox"]',
    '[role="combobox"]',
    '[role="menuitem"]',
    '[role="tab"]',
    '[role="link"]',
    '[contenteditable="true"]',
    'a[href]',
  ];

  const set = new Set();
  selectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => set.add(el));
  });

  // Clickable divs/spans: cursor:pointer, leaf nodes only, to avoid grabbing
  // every container in the layout.
  document.querySelectorAll('div, span').forEach(el => {
    if (set.has(el)) return;
    const style = window.getComputedStyle(el);
    if (style.cursor !== 'pointer') return;
    if (el.childElementCount > 0) return;
    set.add(el);
  });

  const safeAttr = (el, name) => {
    const v = el.getAttribute(name);
    return v === null || v === '' ? null : v;
  };

  const cleanClass = (el) => {
    if (!el.className) return null;
    if (typeof el.className !== 'string') return null;
    const t = el.className.trim();
    return t === '' ? null : t;
  };

  return Array.from(set).map(el => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible =
      rect.width > 0 &&
      rect.height > 0 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none' &&
      parseFloat(style.opacity || '1') > 0;

    return {
      tag: el.tagName.toLowerCase(),
      text: ((el.innerText || el.textContent || '').trim()).slice(0, 200),
      placeholder: safeAttr(el, 'placeholder'),
      aria_label: safeAttr(el, 'aria-label'),
      aria_labelledby: safeAttr(el, 'aria-labelledby'),
      title: safeAttr(el, 'title'),
      role: safeAttr(el, 'role'),
      id: el.id || null,
      class: cleanClass(el),
      type: safeAttr(el, 'type'),
      name: safeAttr(el, 'name'),
      href: safeAttr(el, 'href'),
      contenteditable: safeAttr(el, 'contenteditable'),
      data_testid: safeAttr(el, 'data-testid'),
      visible: visible,
      enabled: !el.disabled,
      rect: { x: Math.round(rect.x), y: Math.round(rect.y),
              w: Math.round(rect.width), h: Math.round(rect.height) },
    };
  });
}
"""


def _suggest_locator(el: dict[str, Any]) -> str:
    """Best-effort Playwright locator string for the element.

    Priority: data-testid > role+name > aria-label > placeholder > id >
    name attr > tag + visible text > tag + first class.
    """
    tag = el.get("tag") or "*"
    role = el.get("role")
    text = (el.get("text") or "").strip()
    type_attr = el.get("type")

    if el.get("data_testid"):
        return f'[data-testid="{el["data_testid"]}"]'

    # File inputs are most useful by type.
    if tag == "input" and type_attr == "file":
        return 'input[type="file"]'

    accessible_name = el.get("aria_label") or (text if len(text) <= 60 else None)

    if role and accessible_name:
        return f'role={role}[name="{_escape_quotes(accessible_name)}"]'

    # Treat <button>, <a>, contenteditable as their implicit role.
    implicit_role = {
        "button": "button",
        "a": "link",
        "textarea": "textbox",
    }.get(tag)
    if implicit_role and accessible_name:
        return f'role={implicit_role}[name="{_escape_quotes(accessible_name)}"]'

    if el.get("aria_label"):
        return f'[aria-label="{_escape_quotes(el["aria_label"])}"]'

    if el.get("placeholder"):
        return f'{tag}[placeholder="{_escape_quotes(el["placeholder"])}"]'

    if el.get("id"):
        return f'#{el["id"]}'

    if el.get("name"):
        return f'{tag}[name="{_escape_quotes(el["name"])}"]'

    if text and len(text) <= 60 and tag in {"button", "a", "div", "span"}:
        return f'{tag}:has-text("{_escape_quotes(text)}")'

    if el.get("class"):
        first = el["class"].split()[0]
        return f'{tag}.{first}'

    return tag


def _escape_quotes(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _classify(el: dict[str, Any]) -> str:
    tag = el.get("tag")
    role = el.get("role")
    type_attr = el.get("type")
    if tag == "input" and type_attr == "file":
        return "file_input"
    if tag == "textarea":
        return "textarea"
    if tag == "input":
        return f"input[{type_attr or 'text'}]"
    if tag == "button" or role == "button":
        return "button"
    if el.get("contenteditable") == "true":
        return "contenteditable"
    if role:
        return f"role={role}"
    if tag == "a":
        return "link"
    return f"{tag}"


def run_debug_selectors(settings: Settings, logger: logging.Logger) -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = settings.logs_dir / f"selector_debug_{timestamp}.png"
    report_path = settings.logs_dir / f"selector_report_{timestamp}.json"

    with open_flow_browser(settings, logger) as session:
        page = acquire_flow_page(session, settings, logger)
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

        logger.info("Waiting 3s for the page to settle...")
        page.wait_for_timeout(3_000)

        page_url = page.url
        page_title = _safe_title(page)
        logger.info("Inspecting page: %s (%s)", page_title, page_url)

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info("Saved screenshot: %s", screenshot_path)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to save screenshot")

        raw_elements = page.evaluate(_HARVEST_JS)

    enriched = _enrich_and_sort(raw_elements)

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "url": page_url,
        "title": page_title,
        "element_count": len(enriched),
        "visible_count": sum(1 for e in enriched if e["visible"]),
        "elements": enriched,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved selector report: %s", report_path)

    _print_summary(report, screenshot_path, report_path)
    return 0


def _safe_title(page: Page) -> str:
    try:
        return page.title()
    except Exception:  # noqa: BLE001
        return "<unavailable>"


def _enrich_and_sort(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for el in elements:
        el["category"] = _classify(el)
        el["suggested_locator"] = _suggest_locator(el)
        enriched.append(el)
    # Visible first, then by category, then by Y position so top-of-page wins.
    enriched.sort(key=lambda e: (not e["visible"], e["category"], e.get("rect", {}).get("y", 0)))
    return enriched


def _print_summary(report: dict[str, Any], screenshot: Path, report_path: Path) -> None:
    print("\n=== Selector debug report ===")
    print(f"URL:        {report['url']}")
    print(f"Title:      {report['title']}")
    print(f"Elements:   {report['element_count']} total, {report['visible_count']} visible")
    print(f"Screenshot: {screenshot}")
    print(f"JSON:       {report_path}\n")

    categories = Counter(
        e["category"] for e in report["elements"] if e["visible"]
    )
    print("Visible interactive elements by category:")
    for category, count in sorted(categories.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {count:>3}  {category}")

    print("\nTop candidates worth checking first:")
    _print_top_candidates(report["elements"])


def _print_top_candidates(elements: list[dict[str, Any]]) -> None:
    """Show the elements most likely to be Flow Labs' upload / prompt / generate."""
    visible = [e for e in elements if e["visible"]]

    def show(label: str, matches: list[dict[str, Any]], limit: int = 3) -> None:
        if not matches:
            return
        print(f"\n  [{label}]")
        for e in matches[:limit]:
            text = (e.get("text") or e.get("aria_label") or e.get("placeholder") or "")[:60]
            print(f"    {e['suggested_locator']}   — {text!r}")

    show(
        "file inputs (image upload)",
        [e for e in visible if e["category"] == "file_input"]
        + [e for e in elements if e["category"] == "file_input" and not e["visible"]],
        limit=5,
    )
    show(
        "textareas / contenteditables (prompt box)",
        [e for e in visible if e["category"] in {"textarea", "contenteditable"}
         or (e["category"].startswith("input[") and "text" in e["category"])],
    )
    show(
        "generate-like buttons",
        [
            e for e in visible
            if e["category"] == "button"
            and any(kw in (e.get("text", "").lower() + " " + (e.get("aria_label") or "").lower())
                    for kw in ("generate", "create", "run", "make", "submit"))
        ],
    )
    show(
        "download-like buttons",
        [
            e for e in visible
            if e["category"] in {"button", "link"}
            and "download" in (e.get("text", "").lower() + " " + (e.get("aria_label") or "").lower())
        ],
    )
