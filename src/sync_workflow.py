"""Sync the live Flow Labs gallery back into products.csv.

Two commands:

  --capture-tiles
      Scan all `[data-tile-id]` elements on the current Flow page and
      bind them to CSV rows. Rows that already have a `flow_media_id`
      stay put; the remaining tiles are assigned to image_submitted
      rows that have no media_id yet, in submission order (newest tile
      → most recently submitted row). The caller is reminded that this
      ordering assumes a single-batch session and can be wrong if
      multiple batches were interleaved.

  --sync-favorites
      Scan tiles, find favorited ones, look up matching CSV rows by
      `flow_media_id`, and flip those rows to `image_approved`.
      Rows without a captured `flow_media_id` cannot be matched —
      run --capture-tiles first.

Both commands open the live Chrome via the existing browser session
(remote-debug mode); they never close it.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from .config import Settings
from .csv_workflow import (
    CsvRow,
    MEDIA_ID_SEPARATOR,
    STATUS_IMAGE_APPROVED,
    STATUS_IMAGE_SUBMITTED,
    _join_unique,
    load_csv,
    media_ids_of,
    save_csv,
)


def _tile_ids_of(row: CsvRow) -> list[str]:
    return [
        s.strip() for s in (row.flow_tile_id or "").split(MEDIA_ID_SEPARATOR)
        if s.strip()
    ]


def _backfill_media_ids_from_tile_ids(
    rows: list[CsvRow],
    tiles: list[TileInfo],
    logger: logging.Logger,
) -> int:
    """For rows with tile_ids but no media_ids, look up media_ids from a
    fresh tile scan and back-fill them.

    This handles the fast-submit aftermath: a submit captured the
    tile_id immediately, but the post-batch sweep ran before Flow
    finished rendering the image so the media_id stayed empty. By the
    time sync-favorites runs, the gallery has settled and we can fill
    in what was missing — making favorite matching robust even when
    the submit-time sweep missed.

    Returns the number of rows updated.
    """
    by_tile_id: dict[str, TileInfo] = {t.flow_tile_id: t for t in tiles if t.flow_tile_id}
    updated = 0
    for row in rows:
        if media_ids_of(row):
            continue
        row_tile_ids = _tile_ids_of(row)
        if not row_tile_ids:
            continue
        new_media: list[str] = []
        new_srcs: list[str] = []
        new_hrefs: list[str] = []
        for tid in row_tile_ids:
            t = by_tile_id.get(tid)
            if t is None:
                continue
            if t.flow_media_id:
                new_media.append(t.flow_media_id)
            if t.flow_image_src:
                new_srcs.append(t.flow_image_src)
            if t.tile_href:
                new_hrefs.append(t.tile_href)
        if new_media:
            row.flow_media_id = _join_unique(row.flow_media_id, new_media)
            row.flow_image_src = _join_unique(row.flow_image_src, new_srcs)
            row.tile_href = _join_unique(row.tile_href, new_hrefs)
            updated += 1
    if updated:
        logger.info(
            "Back-filled media_ids for %d row(s) by tile_id "
            "(fast-submit recovery).", updated,
        )
    return updated
from .flow_automation import acquire_flow_page, open_flow_browser
from .flow_tiles import TileInfo, scan_favorite_diagnostic, scan_tiles
from .unmatched_favorites import (
    UnmatchedFavorite,
    load_unmatched,
    merge_unmatched,
    remove_unmatched,
)


# ---------------------------------------------------------------------------
# --capture-tiles
# ---------------------------------------------------------------------------


def run_capture_tiles(settings: Settings, logger: logging.Logger) -> int:
    if not settings.products_csv.exists():
        logger.error("No %s yet. Generate some images first.", settings.products_csv)
        return 1

    rows = load_csv(settings.products_csv)

    with open_flow_browser(settings, logger) as session:
        page = acquire_flow_page(session, settings, logger)
        tiles = scan_tiles(page, logger=logger)

    logger.info("Scanned %d tile(s) on %s", len(tiles), settings.flow_labs_url)
    if not tiles:
        logger.warning("No tiles found. Is the Flow Labs project visible?")
        return 1

    # Tiles whose media_id is already bound to a CSV row should be skipped.
    # Rows can hold multiple `;`-separated media_ids (one per variant), so
    # we flatten before building the set.
    bound_ids: set[str] = set()
    for r in rows:
        for mid in media_ids_of(r):
            bound_ids.add(mid)
    free_tiles = [t for t in tiles if t.flow_media_id and t.flow_media_id not in bound_ids]

    # Candidate rows: image_submitted, no flow_media_id yet. Sort by row
    # order in the CSV — the assumption is that the user submitted rows
    # top-down. Flow's gallery renders newest first, so we reverse the
    # free-tile list to match.
    candidate_row_indices = [
        i for i, r in enumerate(rows)
        if r.status == STATUS_IMAGE_SUBMITTED and not media_ids_of(r)
    ]
    free_tiles_oldest_first = list(reversed(free_tiles))

    # If Flow's project was producing N variants per click, we'll have
    # N × candidate_row_count tiles. Picking every Nth from oldest-first
    # gets us one tile per product instead of binding two adjacent
    # variants to two different products.
    stride = 1
    if (
        candidate_row_indices
        and free_tiles_oldest_first
        and len(free_tiles_oldest_first) % len(candidate_row_indices) == 0
    ):
        stride = len(free_tiles_oldest_first) // len(candidate_row_indices)
        if stride > 1:
            logger.info(
                "Detected ~%d variants per row; binding every %dth tile.",
                stride, stride,
            )
            free_tiles_oldest_first = free_tiles_oldest_first[::stride]

    n_bind = min(len(candidate_row_indices), len(free_tiles_oldest_first))
    for slot, tile in zip(candidate_row_indices, free_tiles_oldest_first):
        _apply_tile_to_row(rows[slot], tile)
    if n_bind > 0:
        save_csv(settings.products_csv, rows)

    logger.info(
        "Bound %d tile(s) to image_submitted row(s) by submission order.", n_bind
    )
    leftover_tiles = len(free_tiles_oldest_first) - n_bind
    leftover_rows = len(candidate_row_indices) - n_bind
    if leftover_tiles:
        logger.info(
            "%d tile(s) had no matching row to bind to.", leftover_tiles
        )
    if leftover_rows:
        logger.info(
            "%d image_submitted row(s) still have no flow_media_id.", leftover_rows
        )

    favorited = sum(1 for t in tiles if t.favorited)
    if favorited:
        logger.info(
            "Hint: %d tile(s) are currently favorited. Run --sync-favorites "
            "to flip the matching rows to image_approved.", favorited
        )
    return 0


def _apply_tile_to_row(row: CsvRow, tile: TileInfo) -> None:
    row.flow_tile_id = tile.flow_tile_id or row.flow_tile_id
    row.flow_image_src = tile.flow_image_src or row.flow_image_src
    row.flow_media_id = tile.flow_media_id or row.flow_media_id
    row.tile_href = tile.tile_href or row.tile_href


def _promote_to_first(existing: str, target: str) -> str:
    """Move `target` to the front of a `;`-separated list, preserving the rest."""
    ids = [m.strip() for m in (existing or "").split(MEDIA_ID_SEPARATOR) if m.strip()]
    if target in ids:
        ids.remove(target)
    if target:
        ids.insert(0, target)
    return MEDIA_ID_SEPARATOR.join(ids)


# ---------------------------------------------------------------------------
# --sync-favorites
# ---------------------------------------------------------------------------


def run_sync_favorites(
    settings: Settings,
    logger: logging.Logger,
    *,
    approve_only: bool = True,
    auto_bind_unmatched: bool = True,
) -> int:
    """Scan favorited tiles and flip matching CSV rows to image_approved.

    Non-favorited rows are left at image_submitted.

    Match phases:
      1. Direct: favorited media_id in row.flow_media_id (one or many
         ;-separated) → flip that row to image_approved.
      2. Auto-bind (default ON): if some favorited tiles couldn't be
         matched AND there are image_submitted rows whose captured
         media_ids don't include any favorited id, pair them 1:1 in
         submission order — oldest hearted tile (DOM bottom) to the
         lowest-id row. Triggers ONLY when both counts are equal, so a
         partial heart batch doesn't silently misbind. Pass
         `auto_bind_unmatched=False` to disable.
    """
    if not settings.products_csv.exists():
        logger.error("No %s yet. Generate some images first.", settings.products_csv)
        return 1

    rows = load_csv(settings.products_csv)

    with open_flow_browser(settings, logger) as session:
        page = acquire_flow_page(session, settings, logger)
        tiles = scan_tiles(page, logger=logger)
        # Pull diagnostic state from the same JS run before the browser
        # context unwinds — used only when 0 favorites were found.
        diagnostic = scan_favorite_diagnostic(page) if not any(
            t.favorited for t in tiles
        ) else {"candidates": [], "evidence": []}

    # Only image tiles are eligible for video animation. Video tiles
    # (kind="video") may be favorited too, but those represent
    # already-animated outputs and should not flip a CSV row to
    # image_approved. Tiles with empty `kind` are legacy/unknown and
    # we accept them for backwards compatibility.
    favorited_image_tiles = [
        t for t in tiles
        if t.favorited and t.flow_media_id and t.kind in ("image", "")
    ]
    favorited_ids = {t.flow_media_id for t in favorited_image_tiles}
    skipped_video_favorites = sum(
        1 for t in tiles if t.favorited and t.flow_media_id and t.kind == "video"
    )
    mode = "approve-only" if approve_only else "full"
    logger.info(
        "Sync mode=%s — scanned %d tile(s); %d favorited image(s); "
        "%d favorited video(s) skipped.",
        mode, len(tiles), len(favorited_ids), skipped_video_favorites,
    )

    # Fast-submit aftermath: rows may have a tile_id but no media_id.
    # Back-fill the media_id from the fresh scan before we try to
    # match — without this, every favorite would look unmatched.
    backfilled = _backfill_media_ids_from_tile_ids(rows, tiles, logger)
    if backfilled:
        save_csv(settings.products_csv, rows)

    if not favorited_ids:
        _print_favorite_diagnostic(diagnostic, settings, logger)
        return 0

    # Which CSV rows can we match? A row may have several media_ids
    # (variants) stored as `;`-separated; index each one back to the row.
    # We also build a tile_id index so we can fall back when a row knows
    # the tile (from fast-submit capture) but doesn't know its media_id
    # yet — common when the post-batch sweep missed.
    rows_by_media_id: dict[str, int] = {}
    rows_by_tile_id: dict[str, int] = {}
    for i, r in enumerate(rows):
        for mid in media_ids_of(r):
            rows_by_media_id[mid] = i
        for tid in _tile_ids_of(r):
            rows_by_tile_id[tid] = i

    # Per-tile lookup for the fallback matcher.
    favorited_tile_by_id: dict[str, TileInfo] = {
        t.flow_media_id: t for t in favorited_image_tiles
    }

    approved_now: list[CsvRow] = []
    unmatched_ids: list[str] = []
    already_done: list[str] = []

    for mid in favorited_ids:
        idx = rows_by_media_id.get(mid)
        if idx is None:
            # Tile_id fallback: the favorite's tile may already be on a
            # row even though its media_id never got written there.
            t = favorited_tile_by_id.get(mid)
            if t and t.flow_tile_id:
                idx = rows_by_tile_id.get(t.flow_tile_id)
                if idx is not None:
                    # Write the media_id onto the row now so future
                    # passes match by the fast path. Index it too.
                    rows[idx].flow_media_id = _join_unique(
                        rows[idx].flow_media_id, [mid]
                    )
                    if t.flow_image_src:
                        rows[idx].flow_image_src = _join_unique(
                            rows[idx].flow_image_src, [t.flow_image_src]
                        )
                    if t.tile_href:
                        rows[idx].tile_href = _join_unique(
                            rows[idx].tile_href, [t.tile_href]
                        )
                    rows_by_media_id[mid] = idx
                    logger.info(
                        "Matched favorite media_id=%s to row id=%s via "
                        "tile_id fallback.", mid[:8], rows[idx].id or "?",
                    )
        if idx is None:
            unmatched_ids.append(mid)
            continue
        row = rows[idx]
        # Promote the hearted media_id to the front of the row's list so
        # downstream video animation targets the variant the user liked.
        row.flow_media_id = _promote_to_first(row.flow_media_id, mid)
        if row.status == STATUS_IMAGE_APPROVED:
            already_done.append(mid)
            continue
        # Don't downgrade rows that progressed past image_submitted.
        if row.status not in (STATUS_IMAGE_SUBMITTED, "pending"):
            logger.info(
                "Skipping row id=%s (status=%s; not flipping to image_approved).",
                row.id or "?", row.status,
            )
            continue
        row.status = STATUS_IMAGE_APPROVED
        approved_now.append(row)

    # Auto-bind: handle the common "stale captures, fresh hearts" case
    # where the row.flow_media_id was captured for one variant but the
    # user hearted a different variant of the same generation. Pair 1:1
    # in submission order when both unmatched counts are equal.
    if auto_bind_unmatched and unmatched_ids:
        favorited_set = set(favorited_ids)
        unmatched_row_indices: list[int] = [
            i for i, r in enumerate(rows)
            if r.status == STATUS_IMAGE_SUBMITTED
            and not (set(media_ids_of(r)) & favorited_set)
        ]
        if unmatched_row_indices and len(unmatched_ids) == len(unmatched_row_indices):
            # DOM order is newest-first; reverse so oldest-hearted-tile
            # pairs with the lowest-id row.
            favorited_oldest_first = [
                t for t in reversed(tiles)
                if t.favorited and t.flow_media_id in set(unmatched_ids)
            ]
            logger.info(
                "Auto-binding %d unmatched favorited tile(s) to %d unmatched row(s) in submission order.",
                len(favorited_oldest_first),
                len(unmatched_row_indices),
            )
            still_unmatched: list[str] = list(unmatched_ids)
            for row_idx, tile in zip(unmatched_row_indices, favorited_oldest_first):
                row = rows[row_idx]
                row.flow_media_id = _promote_to_first(
                    row.flow_media_id, tile.flow_media_id
                )
                if row.status == STATUS_IMAGE_SUBMITTED:
                    row.status = STATUS_IMAGE_APPROVED
                    approved_now.append(row)
                if tile.flow_media_id in still_unmatched:
                    still_unmatched.remove(tile.flow_media_id)
                logger.info(
                    "  + %s  <->  id=%s  %s",
                    tile.flow_media_id[:8],
                    row.id or "-",
                    row.product_name,
                )
            unmatched_ids = still_unmatched
        elif unmatched_row_indices:
            logger.info(
                "Auto-bind skipped: %d unmatched favorited tile(s) vs "
                "%d unmatched row(s). Counts differ; run "
                "_remap_favorites.py for manual mapping.",
                len(unmatched_ids),
                len(unmatched_row_indices),
            )

    if approved_now:
        save_csv(settings.products_csv, rows)

    # Persist any favorites that still didn't bind to a row. These show
    # up in the UI's "Unmatched Favorited Images" section so the user
    # can manually map a manually-regenerated variant to its product.
    # (favorited_tile_by_id was already built above for the matcher.)
    unmatched_entries: list[UnmatchedFavorite] = []
    for mid in unmatched_ids:
        t = favorited_tile_by_id.get(mid)
        unmatched_entries.append(
            UnmatchedFavorite(
                media_id=mid,
                flow_tile_id=(t.flow_tile_id if t else ""),
                flow_image_src=(t.flow_image_src if t else ""),
                tile_href=(t.tile_href if t else ""),
                edit_id=(t.edit_id if t else ""),
            )
        )
    if unmatched_entries:
        merge_unmatched(unmatched_entries)
        logger.info(
            "Persisted %d unmatched favorite(s) to data/unmatched_favorites.json.",
            len(unmatched_entries),
        )
    else:
        # All recently-favorited tiles matched a row; we still leave any
        # previously-stashed unmatched entries on disk for the user to
        # bind. We do NOT clear them here.
        pass

    logger.info(
        "Approved %d row(s) (%d were already approved).",
        len(approved_now), len(already_done),
    )
    print(f"\nApproved {len(approved_now)} product(s):")
    if approved_now:
        for row in approved_now:
            print(f"  id={row.id or '-':6}  {row.product_name}  (media_id={row.flow_media_id})")
    else:
        print("  (none)")

    if unmatched_ids:
        logger.warning(
            "%d favorited tile(s) had no matching CSV row. Run --capture-tiles "
            "first so flow_media_id is populated:",
            len(unmatched_ids),
        )
        for mid in sorted(unmatched_ids):
            logger.warning("  ? %s", mid)

    no_media_rows = sum(
        1 for r in rows
        if r.status == STATUS_IMAGE_SUBMITTED and not r.flow_media_id
    )
    if no_media_rows:
        logger.info(
            "%d image_submitted row(s) still have no flow_media_id (run "
            "--capture-tiles to bind them).", no_media_rows
        )

    # Keep this for debugging / external tooling — silently dump the snapshot
    # we just observed so the user can audit.
    _dump_last_scan(settings.logs_dir, tiles)
    return 0


def _print_favorite_diagnostic(diagnostic: dict, settings, logger: logging.Logger) -> None:
    """Print why 0 favorites were detected so the heuristic can be tuned."""
    candidates = diagnostic.get("candidates", [])
    print("\nNo favorited tiles detected. Diagnostic:")
    print(f"  Found {len(candidates)} favorite-related button(s) on the page.")
    if not candidates:
        print(
            "  None of the buttons looked favorite-shaped. The heart control "
            "may be on a different element type or hidden until hover.\n"
            "  Try hovering over a favorited tile in Flow before re-running "
            "to keep the button in the DOM, or send me a fresh "
            "--debug-selectors / --record-actions capture of the heart UI."
        )
    else:
        # Print up to 8 candidates so the user can see what we saw.
        for i, c in enumerate(candidates[:8]):
            print(
                f"    [{i+1}] tile_id={c.get('tile_id')!r}"
                f" icon={c.get('icon_text')!r}"
                f" aria-pressed={c.get('aria_pressed')!r}"
                f" data-state={c.get('data_state')!r}"
                f" filled={c.get('filled')}"
                f" looks_favorited={c.get('looks_favorited')}"
                f" aria-label={(c.get('aria_label') or '')[:60]!r}"
            )
        if len(candidates) > 8:
            print(f"    … and {len(candidates) - 8} more (full dump saved)")
    try:
        import json
        path = settings.logs_dir / "last_favorite_diagnostic.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(diagnostic, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  Full dump: {path}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write favorite diagnostic: %s", exc)


def bind_unmatched_favorite_to_product(
    settings: Settings,
    media_id: str,
    product_id: str,
    logger: logging.Logger,
) -> tuple[bool, str]:
    """Bind an unmatched-favorite media_id to a CSV row by id.

    Adds media_id to the row's flow_media_id list (semicolon-separated),
    promotes it to the front (so video flow targets it), flips the row
    to image_approved if it was image_submitted, and removes the entry
    from data/unmatched_favorites.json.

    Returns (ok, message). Both UI and CLI consumers use this.
    """
    media_id = (media_id or "").strip()
    product_id = (product_id or "").strip()
    if not media_id or not product_id:
        return False, "media_id and product_id are both required."
    if not settings.products_csv.exists():
        return False, f"No CSV at {settings.products_csv}."
    rows = load_csv(settings.products_csv)
    target = next((r for r in rows if r.id == product_id), None)
    if target is None:
        return False, f"No CSV row with id={product_id!r}."

    target.flow_media_id = _promote_to_first(target.flow_media_id, media_id)
    flipped = False
    if target.status in (STATUS_IMAGE_SUBMITTED, "pending"):
        target.status = STATUS_IMAGE_APPROVED
        flipped = True
    save_csv(settings.products_csv, rows)

    removed = remove_unmatched(media_id)
    logger.info(
        "Bound media_id=%s to product id=%s (%s)%s%s",
        media_id, product_id, target.product_name or "(unnamed)",
        " — flipped to image_approved" if flipped else "",
        " — removed from unmatched" if removed else "",
    )
    return True, (
        f"Bound to id={product_id}"
        + (" (status -> image_approved)" if flipped else "")
        + (" — removed from unmatched_favorites.json" if removed else "")
    )


def run_list_unmatched_favorites(settings: Settings, logger: logging.Logger) -> int:
    """CLI: print the on-disk unmatched-favorites state to stdout."""
    items = load_unmatched()
    if not items:
        print("No unmatched favorites.")
        return 0
    print(f"{len(items)} unmatched favorite(s):")
    for it in items:
        print(f"  media_id: {it.media_id}")
        print(f"    tile_id:  {it.flow_tile_id}")
        print(f"    edit_id:  {it.edit_id}")
        print(f"    detected: {it.detected_at}")
        if it.tile_href:
            print(f"    href:     {it.tile_href}")
        print()
    return 0


def run_bind_favorite(
    settings: Settings,
    logger: logging.Logger,
    media_id: str,
    product_id: str,
) -> int:
    ok, msg = bind_unmatched_favorite_to_product(
        settings, media_id, product_id, logger
    )
    if ok:
        print(msg)
        return 0
    print(f"FAILED: {msg}")
    return 1


def _dump_last_scan(logs_dir, tiles: list[TileInfo]) -> None:
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / "last_tile_scan.json"
        import json
        path.write_text(
            json.dumps([asdict(t) for t in tiles], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass
