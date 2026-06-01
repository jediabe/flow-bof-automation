"""CSV-based product workflow.

The daily flow:
    1. Drop new product images into inputs/incoming_images/.
    2. `python main.py --scan-images [--category X]` adds one CSV row per
       new image (filename → product_name, default status `pending`).
    3. `python main.py --list-status` prints status counts + pending rows.
    4. `python main.py --generate-images --limit N` processes up to N
       pending rows: upload → attach → prompt → click generate, then
       marks status `image_submitted` and writes CSV after each row.

JSON workflow (`inputs/products.json`) is still supported by the older
commands (--dry-run, --limit, --product-index, --run-one); CSV is the
primary path going forward.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import Settings
from .flow_automation import (
    FlowAutomationError,
    acquire_flow_page,
    generate_image_for_product,
    open_flow_browser,
)
from .prompt_builder import build_prompt
from .recorded_flow import ensure_variants_1x, sweep_fill_media_ids
from .utils import (
    Product,
    next_output_paths,
    output_dir_for,
    product_from_dict,
    validate_product_image,
)


# Allowed status values for the `status` column. The CSV loader will warn
# (not crash) if it sees something else, so manual edits can use any string.
STATUS_PENDING = "pending"
STATUS_IMAGE_SUBMITTED = "image_submitted"
STATUS_IMAGE_APPROVED = "image_approved"
STATUS_IMAGE_REJECTED = "image_rejected"
STATUS_VIDEO_PENDING = "video_pending"
STATUS_VIDEO_SUBMITTED = "video_submitted"
STATUS_VIDEO_ERROR = "video_error"
STATUS_DONE = "done"

ALLOWED_STATUSES: set[str] = {
    STATUS_PENDING,
    STATUS_IMAGE_SUBMITTED,
    STATUS_IMAGE_APPROVED,
    STATUS_IMAGE_REJECTED,
    STATUS_VIDEO_PENDING,
    STATUS_VIDEO_SUBMITTED,
    STATUS_VIDEO_ERROR,
    STATUS_DONE,
}

IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class CsvRow:
    # `id` is set by the manifest workflow (e.g. "01"); blank for scan-flow rows.
    id: str = ""
    product_name: str = ""
    image_path: str = ""
    # `image_prompt` (set by manifest) takes priority over `prompt_override`
    # over the universal prompt. `video_prompt` is captured for the future
    # image-to-video pass.
    image_prompt: str = ""
    video_prompt: str = ""
    status: str = STATUS_PENDING
    # Populated by --capture-tiles / --sync-favorites by scanning the live
    # Flow Labs page; never derived from the local filename.
    flow_tile_id: str = ""
    flow_image_src: str = ""
    flow_media_id: str = ""
    tile_href: str = ""
    category: str = "auto"
    store: str = ""
    placement_type: str = ""
    prompt_override: str = ""
    notes: str = ""


CSV_FIELDNAMES: list[str] = [f.name for f in fields(CsvRow)]


# ---------------------------------------------------------------------------
# Multi-value helpers
#
# Several `flow_*` columns may hold MULTIPLE values separated by ';' — Flow
# emits N variants per click and we capture them all so sync-favorites can
# match by membership instead of equality.
# ---------------------------------------------------------------------------

MEDIA_ID_SEPARATOR = ";"


def media_ids_of(row: CsvRow) -> list[str]:
    return [
        m.strip()
        for m in (row.flow_media_id or "").split(MEDIA_ID_SEPARATOR)
        if m.strip()
    ]


def primary_media_id(row: CsvRow) -> str:
    ids = media_ids_of(row)
    return ids[0] if ids else ""


def _join_unique(existing: str, additions: list[str]) -> str:
    """Append `additions` to a `;`-separated string, preserving order and dedup."""
    seen: list[str] = [
        m.strip() for m in existing.split(MEDIA_ID_SEPARATOR) if m.strip()
    ]
    seen_set = set(seen)
    for a in additions:
        a = a.strip()
        if a and a not in seen_set:
            seen.append(a)
            seen_set.add(a)
    return MEDIA_ID_SEPARATOR.join(seen)


# ---------------------------------------------------------------------------
# CSV load / save
# ---------------------------------------------------------------------------


def load_csv(path: Path) -> list[CsvRow]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[CsvRow] = []
        for raw in reader:
            rows.append(
                CsvRow(
                    id=(raw.get("id") or "").strip(),
                    product_name=(raw.get("product_name") or "").strip(),
                    image_path=(raw.get("image_path") or "").strip(),
                    image_prompt=(raw.get("image_prompt") or "").rstrip(),
                    video_prompt=(raw.get("video_prompt") or "").rstrip(),
                    status=(raw.get("status") or STATUS_PENDING).strip() or STATUS_PENDING,
                    flow_tile_id=(raw.get("flow_tile_id") or "").strip(),
                    flow_image_src=(raw.get("flow_image_src") or "").strip(),
                    flow_media_id=(raw.get("flow_media_id") or "").strip(),
                    tile_href=(raw.get("tile_href") or "").strip(),
                    category=(raw.get("category") or "auto").strip().lower() or "auto",
                    store=(raw.get("store") or "").strip(),
                    placement_type=(raw.get("placement_type") or "").strip(),
                    prompt_override=(raw.get("prompt_override") or "").rstrip(),
                    notes=(raw.get("notes") or "").strip(),
                )
            )
        return rows


def save_csv(path: Path, rows: list[CsvRow]) -> None:
    """Atomic write — write to a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))
        try:
            os.replace(tmp_name, path)
        except PermissionError as exc:
            # On Windows, Excel and some IDEs take an exclusive lock on
            # the CSV while it's open. The replace then fails. Surface a
            # clear message instead of dumping the WinError.
            raise PermissionError(
                f"Could not write {path}: the file appears to be open in "
                f"another program (Excel, the IDE preview, etc.). Close it "
                f"and re-run the command. The previous values are unchanged. "
                f"(Original: {exc})"
            ) from exc
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Filename → product name
# ---------------------------------------------------------------------------


_WORD_SEPARATORS = re.compile(r"[-_\s]+")


def filename_to_product_name(path: Path) -> str:
    stem = path.stem
    words = [w for w in _WORD_SEPARATORS.split(stem) if w]
    if not words:
        return path.stem or "Product"
    return " ".join(_smart_title(w) for w in words)


def _smart_title(word: str) -> str:
    """Capitalize first letter unless the word already has mixed case.

    str.title() would turn "iPhone" into "Iphone". We preserve any
    already-mixed-case word (iPhone, eBook, GoPro) untouched and only
    capitalize fully-lowercase words.
    """
    if not word:
        return word
    if any(c.isupper() for c in word[1:]):
        return word
    return word[0].upper() + word[1:]


# ---------------------------------------------------------------------------
# --scan-images
# ---------------------------------------------------------------------------


def run_scan_images(settings: Settings, logger: logging.Logger) -> int:
    incoming = settings.incoming_images_dir
    if not incoming.exists():
        logger.error("Incoming images directory not found: %s", incoming)
        return 1

    rows = load_csv(settings.products_csv)
    known_paths = {_norm_path(r.image_path) for r in rows if r.image_path}

    discovered = sorted(
        p for p in incoming.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )

    new_rows: list[CsvRow] = []
    for image in discovered:
        rel = image.relative_to(settings.repo_root).as_posix()
        if _norm_path(rel) in known_paths:
            continue
        new_rows.append(
            CsvRow(
                product_name=filename_to_product_name(image),
                category="auto",
                image_path=rel,
                status=STATUS_PENDING,
            )
        )

    if not new_rows:
        logger.info("No new images in %s (CSV already up to date).", incoming)
        if not settings.products_csv.exists():
            save_csv(settings.products_csv, rows)
            logger.info("Created empty %s", settings.products_csv)
        return 0

    rows.extend(new_rows)
    save_csv(settings.products_csv, rows)

    logger.info("Added %d row(s) to %s", len(new_rows), settings.products_csv)
    for row in new_rows:
        logger.info("  + %s  ←  %s", row.product_name, row.image_path)
    return 0


def _norm_path(value: str) -> str:
    return value.replace("\\", "/").strip().lower()


# ---------------------------------------------------------------------------
# --list-status
# ---------------------------------------------------------------------------


def run_list_status(settings: Settings, logger: logging.Logger) -> int:
    if not settings.products_csv.exists():
        logger.warning(
            "No %s yet. Run --scan-images first.", settings.products_csv
        )
        return 1

    rows = load_csv(settings.products_csv)
    if not rows:
        print(f"{settings.products_csv} has no rows yet.")
        return 0

    counts: Counter[str] = Counter(r.status for r in rows)
    print(f"{settings.products_csv}: {len(rows)} row(s)")
    for status in (
        STATUS_PENDING,
        STATUS_IMAGE_SUBMITTED,
        STATUS_IMAGE_APPROVED,
        STATUS_IMAGE_REJECTED,
        STATUS_VIDEO_PENDING,
        STATUS_VIDEO_SUBMITTED,
        STATUS_DONE,
    ):
        if counts.get(status):
            print(f"  {counts[status]:>4}  {status}")
    unknown = [s for s in counts if s not in ALLOWED_STATUSES]
    for s in sorted(unknown):
        print(f"  {counts[s]:>4}  {s}  (unknown status)")

    pending = [r for r in rows if r.status == STATUS_PENDING]
    if pending:
        print(f"\nPending ({len(pending)}):")
        for r in pending:
            print(f"  - {r.product_name}  [{r.category}]  {r.image_path}")
    return 0


# ---------------------------------------------------------------------------
# --generate-images
# ---------------------------------------------------------------------------


def run_generate_images(
    settings: Settings, logger: logging.Logger, limit: int | None = None
) -> int:
    if not settings.products_csv.exists():
        logger.error("No %s yet. Run --scan-images first.", settings.products_csv)
        return 1

    rows = load_csv(settings.products_csv)
    pending = [i for i, r in enumerate(rows) if r.status == STATUS_PENDING]
    if not pending:
        logger.info("No pending rows.")
        return 0

    if limit is not None:
        pending = pending[:limit]

    return process_rows(settings, logger, rows, pending)


def process_rows(
    settings: Settings,
    logger: logging.Logger,
    rows: list[CsvRow],
    indices: list[int],
) -> int:
    """Run the upload→attach→prompt→generate sequence for each row index.

    Updates `rows[idx].status` to image_submitted on success or appends
    an error tail to `rows[idx].notes` on failure, then atomically
    rewrites the CSV after every row. Returns exit code (0 = all good).
    """
    if not indices:
        logger.info("No rows to process.")
        return 0

    logger.info("Generating images for %d row(s).", len(indices))
    failures = 0
    with open_flow_browser(settings, logger) as session:
        # Pin variants = 1x once per batch so each generate produces a
        # single tile (avoids duplicate-media_id captures across rows).
        try:
            page = acquire_flow_page(session, settings, logger)
            ensure_variants_1x(
                page,
                logger=logger,
                selector_timeout_ms=settings.selector_timeout_ms,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ensure_variants_1x raised; continuing anyway.")

        for n, idx in enumerate(indices, start=1):
            row = rows[idx]
            tag = f"id={row.id} " if row.id else ""
            logger.info(
                "--- [%d/%d] %s%s (%s)", n, len(indices), tag, row.product_name, row.category
            )
            try:
                product = _csv_row_to_product(row, settings)
                validate_product_image(product)
            except (FileNotFoundError, ValueError) as exc:
                failures += 1
                logger.error("Skipping %s: %s", row.product_name, exc)
                continue

            prompt = build_prompt(product)
            out_dir = output_dir_for(settings, product)
            image_path, prompt_path = next_output_paths(out_dir, product.slug)
            prompt_path.write_text(prompt, encoding="utf-8")

            try:
                tiles = generate_image_for_product(
                    session=session,
                    settings=settings,
                    product=product,
                    prompt=prompt,
                    output_image=image_path,
                    logger=logger,
                )
            except FlowAutomationError as exc:
                failures += 1
                logger.error("Generation failed for %s: %s", row.product_name, exc)
                rows[idx].notes = _append_note(rows[idx].notes, f"err: {exc}")
                save_csv(settings.products_csv, rows)
                continue
            except Exception:  # noqa: BLE001
                failures += 1
                logger.exception("Unexpected error for %s", row.product_name)
                rows[idx].notes = _append_note(rows[idx].notes, "err: unexpected")
                save_csv(settings.products_csv, rows)
                continue

            # In fast-submit mode, tile_ids are populated immediately but
            # media_ids may still be resolving. Store every non-empty
            # field we got; the post-batch sweep below fills the rest.
            tiles_or_empty: list = list(tiles or [])
            captured_tile_ids = [t.flow_tile_id for t in tiles_or_empty if t.flow_tile_id]
            captured_media_ids = [t.flow_media_id for t in tiles_or_empty if t.flow_media_id]
            captured_srcs = [t.flow_image_src for t in tiles_or_empty if t.flow_image_src]
            captured_hrefs = [t.tile_href for t in tiles_or_empty if t.tile_href]

            if captured_tile_ids:
                rows[idx].flow_tile_id = _join_unique(
                    rows[idx].flow_tile_id, captured_tile_ids
                )
            if captured_media_ids:
                rows[idx].flow_media_id = _join_unique(
                    rows[idx].flow_media_id, captured_media_ids
                )
            if captured_srcs:
                rows[idx].flow_image_src = _join_unique(
                    rows[idx].flow_image_src, captured_srcs
                )
            if captured_hrefs:
                rows[idx].tile_href = _join_unique(
                    rows[idx].tile_href, captured_hrefs
                )

            rows[idx].status = STATUS_IMAGE_SUBMITTED
            save_csv(settings.products_csv, rows)
            if captured_media_ids:
                logger.info(
                    "Marked %s = %s (captured %d media_id(s): %s)",
                    row.product_name,
                    STATUS_IMAGE_SUBMITTED,
                    len(captured_media_ids),
                    ", ".join(m[:8] for m in captured_media_ids),
                )
            elif captured_tile_ids:
                logger.info(
                    "Marked %s = %s (captured %d tile_id(s); media_ids "
                    "to be filled by post-batch sweep)",
                    row.product_name,
                    STATUS_IMAGE_SUBMITTED,
                    len(captured_tile_ids),
                )
            else:
                logger.info(
                    "Marked %s = %s (no tile captured)",
                    row.product_name, STATUS_IMAGE_SUBMITTED,
                )

        # --- Post-batch media_id sweep (fast-submit mode only) ----------
        # We left rows with tile_ids but possibly empty media_ids. Now
        # that every row's image has had time to start resolving, scan
        # the gallery once and back-fill media_ids by tile_id.
        if settings.image_fast_submit_mode:
            try:
                page = acquire_flow_page(session, settings, logger)
                rows_with_tiles: dict[int, list[str]] = {}
                for idx in indices:
                    row = rows[idx]
                    pending_tile_ids: list[str] = [
                        s.strip() for s in (row.flow_tile_id or "").split(MEDIA_ID_SEPARATOR)
                        if s.strip()
                    ]
                    have_media: set[str] = {
                        s.strip() for s in (row.flow_media_id or "").split(MEDIA_ID_SEPARATOR)
                        if s.strip()
                    }
                    if pending_tile_ids and not have_media:
                        rows_with_tiles[idx] = pending_tile_ids

                if rows_with_tiles:
                    logger.info(
                        "Post-batch sweep: filling media_ids for %d row(s).",
                        len(rows_with_tiles),
                    )
                    matches = sweep_fill_media_ids(page, rows_with_tiles, logger)
                    filled = 0
                    for idx, tile_infos in matches.items():
                        new_media = [t.flow_media_id for t in tile_infos if t.flow_media_id]
                        new_srcs  = [t.flow_image_src for t in tile_infos if t.flow_image_src]
                        new_hrefs = [t.tile_href      for t in tile_infos if t.tile_href]
                        if new_media:
                            rows[idx].flow_media_id = _join_unique(
                                rows[idx].flow_media_id, new_media
                            )
                            filled += 1
                        if new_srcs:
                            rows[idx].flow_image_src = _join_unique(
                                rows[idx].flow_image_src, new_srcs
                            )
                        if new_hrefs:
                            rows[idx].tile_href = _join_unique(
                                rows[idx].tile_href, new_hrefs
                            )
                    if filled:
                        save_csv(settings.products_csv, rows)
                        logger.info(
                            "Post-batch sweep filled media_ids for %d row(s).",
                            filled,
                        )
                    else:
                        logger.info(
                            "Post-batch sweep found no resolvable media_ids "
                            "(images may still be generating). Re-run later "
                            "via `--capture-tiles` to back-fill."
                        )
            except Exception:  # noqa: BLE001
                logger.exception("Post-batch sweep raised; continuing.")

    return 1 if failures else 0


def _append_note(existing: str, new: str) -> str:
    if not existing:
        return new
    return existing + " | " + new


def _csv_row_to_product(row: CsvRow, settings: Settings) -> Product:
    """Convert a CSV row to the Product the prompt builder/automation expects.

    Precedence for the final prompt: image_prompt (set by manifest) >
    prompt_override (set by hand) > universal prompt (build_prompt default).
    Empty cells become None so product_from_dict can fill them from
    CATEGORY_RULES — harmless even when the universal prompt ignores them.
    """
    override = row.image_prompt or row.prompt_override or None
    raw: dict[str, object] = {
        "product_name": row.product_name,
        "category": row.category or "auto",
        "product_image_path": row.image_path,
        "store": row.store or None,
        "placement_type": row.placement_type or None,
        "prompt_override": override,
    }
    return product_from_dict(raw, settings)
