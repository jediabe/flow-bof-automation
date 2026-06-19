"""Scan generated-image tiles on the live Flow Labs page.

A "tile" is any element with `data-tile-id="fe_id_..."`. Per the
inspected DOM:
    <... data-tile-id="fe_id_...">
        <a href="...">
            <img alt="Generated image"
                 src="/fx/api/trpc/media.getMediaUrlRedirect?name=...">
        </a>
        ... favorite button etc.
    </...>

We extract `flow_tile_id`, `flow_image_src`, `flow_media_id` (the `name`
query param), and `tile_href`, plus a best-effort `favorited` boolean.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from patchright.sync_api import Page


@dataclass
class TileInfo:
    flow_tile_id: str = ""
    flow_image_src: str = ""
    flow_media_id: str = ""
    tile_href: str = ""
    favorited: bool = False
    rect: dict[str, int] = field(default_factory=dict)
    # "image" when the tile renders an <img> from the media-redirect URL,
    # "video" for <video>, "" when we can't tell. Only image tiles are
    # eligible for approval / video animation.
    kind: str = ""
    # The /edit/<id> portion of the tile's anchor href, useful for the
    # unmatched-favorites UI to deep-link back to Flow's editor.
    edit_id: str = ""


# JavaScript that walks every `[data-tile-id]` and returns plain dicts.
# Favorited-state detection finds every `<button>` on the page that looks
# like a favorite control (icon=favorite[_border], or label/text mentions
# "favorite"), decides if it's in the FILLED/ON state, and maps it back
# to its nearest `[data-tile-id]` ancestor. We look page-wide (not just
# inside the tile) because in Flow the heart button often lives in a
# wrapper alongside the tile, not nested inside it.
_TILE_SCAN_JS = r"""
() => {
  const extractMediaId = (src) => {
    if (!src) return '';
    const m = src.match(/[?&]name=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  };

  // Walk ancestors checking computed visibility. The favorited indicator
  // in Flow is rendered as an icon inside a wrapper whose opacity flips
  // from 0 (unfavorited) to 1 (favorited) — so we treat opacity ~ 0 as
  // "not actually shown" regardless of the icon presence.
  const isShown = (el) => {
    let cur = el;
    while (cur && cur.nodeType === 1) {
      const cs = window.getComputedStyle(cur);
      if (cs.display === 'none') return false;
      if (cs.visibility === 'hidden') return false;
      if (parseFloat(cs.opacity || '1') < 0.05) return false;
      cur = cur.parentElement;
    }
    return true;
  };

  // Confirmed DOM (devtools screenshot):
  //   <i class="… google-symbols …">favorite</i>
  // wrapped in divs whose ancestor flips opacity:1 when favorited.
  // The `favorite` ligature is the FILLED heart; the empty state uses
  // `favorite_border` and/or sits behind opacity:0.
  const ICON_SEL = [
    'i.google-symbols',
    'i.material-symbols',
    'i.material-symbols-outlined',
    'i.material-icons',
    '[class*="google-symbols"]',
    '[class*="material-symbols"]',
  ].join(',');

  const favoritedTiles = new Set();
  const evidence = [];
  const candidates = [];

  const allIcons = Array.from(document.querySelectorAll(ICON_SEL));
  for (const icon of allIcons) {
    const text = (icon.textContent || '').trim();
    if (text !== 'favorite' && text !== 'favorited') {
      // Not a filled-heart ligature.
      continue;
    }
    const tile = icon.closest('[data-tile-id]');
    const tileId = tile ? tile.getAttribute('data-tile-id') : null;
    const shown = isShown(icon);

    // Track every filled-heart ligature we see, for diagnostic output.
    candidates.push({
      tile_id: tileId,
      icon_text: text,
      shown: shown,
      looks_favorited: shown && !!tileId,
    });

    if (shown && tileId) {
      favoritedTiles.add(tileId);
      evidence.push({ tile_id: tileId, source: 'i.google-symbols=favorite + visible' });
    }
  }

  // Fallback: legacy button-shaped favorite controls (sites where the
  // heart IS a <button>, not a sibling <div>). Same FILL detection as
  // before, but only used when no icon-based hit was registered.
  if (favoritedTiles.size === 0) {
    const isFilledFvs = (el) => {
      try {
        const fvs = (window.getComputedStyle(el).fontVariationSettings || '');
        return /['"]?FILL['"]?\s*1/.test(fvs);
      } catch (e) { return false; }
    };
    const isPressedState = (b) =>
      b.getAttribute('aria-pressed') === 'true' ||
      b.getAttribute('data-state') === 'on' ||
      b.getAttribute('data-state') === 'checked' ||
      b.getAttribute('data-favorited') === 'true' ||
      /remove from favorit|unfavor|favorited/i.test(b.getAttribute('aria-label') || '');
    for (const b of document.querySelectorAll('button')) {
      const icon = b.querySelector(ICON_SEL);
      const text = icon ? (icon.textContent || '').trim() : '';
      const looksFavorited = (
        (text === 'favorite' || text === 'favorited') &&
        (isFilledFvs(icon) || isPressedState(b) || isShown(icon))
      ) || isPressedState(b);
      if (!looksFavorited) continue;
      const tile = b.closest('[data-tile-id]');
      const tileId = tile ? tile.getAttribute('data-tile-id') : null;
      if (tileId) {
        favoritedTiles.add(tileId);
        evidence.push({ tile_id: tileId, source: 'button-fallback' });
      }
      candidates.push({
        tile_id: tileId, icon_text: text, shown: true, looks_favorited: true,
      });
    }
  }

  // Generated tiles inspected from live Flow DOM use these shapes:
  //   image: <img alt="Generated image" src="/fx/api/trpc/media.getMediaUrlRedirect?name=..."/>
  //   video: <video src="/fx/api/trpc/media.getMediaUrlRedirect?name=..." ... />
  // We detect kind separately so favorite-sync can filter out video
  // tiles (they shouldn't be approved for animation).
  const mediaForTile = (tile) => {
    const img = tile.querySelector('img[alt="Generated image"][src*="media.getMediaUrlRedirect"]')
              || tile.querySelector('img[src*="media.getMediaUrlRedirect"]');
    if (img && img.getAttribute('src')) {
      return { kind: 'image', src: img.getAttribute('src') };
    }
    const video = tile.querySelector('video[src*="media.getMediaUrlRedirect"]')
                || tile.querySelector('video[source][src*="media.getMediaUrlRedirect"]')
                || tile.querySelector('video');
    if (video) {
      let vsrc = video.getAttribute('src') || '';
      if (!vsrc) {
        const source = video.querySelector('source[src*="media.getMediaUrlRedirect"]');
        if (source) vsrc = source.getAttribute('src') || '';
      }
      if (vsrc) return { kind: 'video', src: vsrc };
    }
    // Fall back to any <img> so the tile isn't dropped entirely; mark
    // kind empty so callers can filter.
    const anyImg = tile.querySelector('img');
    if (anyImg) return { kind: '', src: anyImg.getAttribute('src') || '' };
    return { kind: '', src: '' };
  };

  const extractEditId = (href) => {
    if (!href) return '';
    const m = href.match(/\/edit\/([^/?#]+)/);
    return m ? m[1] : '';
  };

  const tiles = Array.from(document.querySelectorAll('[data-tile-id]')).map((t) => {
    const tileId = t.getAttribute('data-tile-id') || '';
    const media = mediaForTile(t);
    const mediaId = extractMediaId(media.src);
    const anchor = t.closest('a[href]') || t.querySelector('a[href]');
    const href = anchor ? (anchor.getAttribute('href') || '') : '';
    const rect = t.getBoundingClientRect();
    return {
      flow_tile_id: tileId,
      flow_image_src: media.src,
      flow_media_id: mediaId,
      tile_href: href,
      favorited: favoritedTiles.has(tileId),
      rect: {
        x: Math.round(rect.x), y: Math.round(rect.y),
        w: Math.round(rect.width), h: Math.round(rect.height),
      },
      kind: media.kind,
      edit_id: extractEditId(href),
    };
  });

  window.__lastFavoriteCandidates = candidates;
  window.__lastFavoriteEvidence = evidence;

  return tiles;
}
"""

_FAV_DIAGNOSTIC_JS = r"""
() => ({
  candidates: (window.__lastFavoriteCandidates || []).slice(0, 25),
  evidence:   (window.__lastFavoriteEvidence   || []).slice(0, 25),
})
"""


def extract_media_id(src: str) -> str:
    """Pull the `name=...` query param out of a Flow media URL.

    Works on relative URLs too (no scheme/host). Returns '' if missing.
    """
    if not src:
        return ""
    # Cheap regex first — avoids parsing relative URLs unnecessarily.
    m = re.search(r"[?&]name=([^&]+)", src)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    try:
        q = parse_qs(urlparse(src).query)
        if "name" in q and q["name"]:
            return q["name"][0]
    except Exception:  # noqa: BLE001
        pass
    return ""


def scan_tiles(page: Page, logger: logging.Logger | None = None) -> list[TileInfo]:
    """Return every visible generated-image tile on the page.

    Order is DOM order (top-to-bottom). Callers that need newest-first
    can reverse the list — Flow's gallery typically renders newest at
    the top, but we do not assume that here.
    """
    try:
        raw = page.evaluate(_TILE_SCAN_JS)
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("Tile scan failed: %s", exc)
        return []

    # Flow's gallery often nests `[data-tile-id]` (a wrapper carrying
    # the drag handle plus an inner card with the actual content), so a
    # naive querySelectorAll('[data-tile-id]') returns each visual tile
    # twice. Dedupe by tile_id, keeping the first encounter — both
    # records resolve the same media_id since the inner <img> is a
    # descendant of either ancestor.
    seen_tile_ids: set[str] = set()
    out: list[TileInfo] = []
    for r in raw:
        tile_id = r.get("flow_tile_id") or ""
        if tile_id and tile_id in seen_tile_ids:
            continue
        if tile_id:
            seen_tile_ids.add(tile_id)
        out.append(
            TileInfo(
                flow_tile_id=tile_id,
                flow_image_src=r.get("flow_image_src") or "",
                flow_media_id=r.get("flow_media_id") or "",
                tile_href=r.get("tile_href") or "",
                favorited=bool(r.get("favorited")),
                rect=r.get("rect") or {},
                kind=r.get("kind") or "",
                edit_id=r.get("edit_id") or "",
            )
        )
    return out


def scan_favorite_diagnostic(page: Page) -> dict:
    """Return the per-button debug data from the last `scan_tiles` call.

    Shape: {"candidates": [...], "evidence": [...]}. `candidates` is
    every favorite-related button we saw on the page with its tile_id,
    icon text, aria-pressed / data-state, computed FILL state, and the
    final `looks_favorited` verdict. `evidence` is the subset that we
    actually counted as favorited.
    """
    try:
        return page.evaluate(_FAV_DIAGNOSTIC_JS) or {"candidates": [], "evidence": []}
    except Exception:  # noqa: BLE001
        return {"candidates": [], "evidence": []}
