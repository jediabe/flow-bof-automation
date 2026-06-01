"""Markdown manifest workflow.

A `prompt_manifest.md` describes products as numbered sections:

    ## 01
    Product Name: Slim Fit Shapewear Bodysuit
    Reference Image: 01.jpg
    Status: pending

    Image Prompt:
    Use the reference image ...

    Video Prompt:
    Slowly orbit around ...

    ---

`--load-manifest` parses the file and creates/updates `products.csv`
with one row per section: `id`, `product_name`, `image_path`,
`image_prompt`, `video_prompt`, `status`. Reference images must already
live in `inputs/reference_images/`. The CSV stays the single source of
truth — manifest is just a friendlier input format.

`--generate-images-from-manifest` then processes pending rows that have
an `image_prompt` set, sending that exact prompt to Flow Labs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .csv_workflow import (
    CsvRow,
    STATUS_IMAGE_APPROVED,
    STATUS_PENDING,
    STATUS_VIDEO_ERROR,
    STATUS_VIDEO_SUBMITTED,
    load_csv,
    media_ids_of,
    primary_media_id,
    process_rows,
    save_csv,
)
from .flow_automation import (
    FlowAutomationError,
    acquire_flow_page,
    open_flow_browser,
)
from .recorded_flow import RecordedFlowError, perform_recorded_video_flow


_SINGLE_LINE_LABELS: set[str] = {"Product Name", "Reference Image", "Status"}
_MULTI_LINE_LABELS: set[str] = {"Image Prompt", "Video Prompt"}
_KNOWN_LABELS: set[str] = _SINGLE_LINE_LABELS | _MULTI_LINE_LABELS

_LABEL_RE = re.compile(r"^\*{0,2}([A-Za-z ]+?)\*{0,2}\s*:\s*(.*)$")

# Section-start patterns. Each captures (id, inline_name_or_empty).
# 1. Markdown headings:  # 01,  ## 01,  ### Product 01,  ## 01: Slim Fit
_HEADER_RE = re.compile(
    r"^#{1,6}\s+(?:[A-Za-z]+\s+)*?(\d{1,3})\b\s*[:.\-)]?\s*(.*)$"
)
# 2. Numbered / dashed lines:  1. Foo,  01 - Foo,  1) Foo
_NUMBERED_RE = re.compile(r"^(\d{1,3})\s*[.\-)]\s*(.*)$")
# 3. Bracketed / parenthesised:  [01],  (01),  [01] Foo
_BRACKETED_RE = re.compile(r"^[\[\(](\d{1,3})[\]\)]\s*[:.\-]?\s*(.*)$")


@dataclass
class ManifestEntry:
    id: str
    product_name: str
    reference_image: str
    status: str
    image_prompt: str
    video_prompt: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_manifest(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    text = path.read_text(encoding="utf-8")

    entries: list[ManifestEntry] = []
    for entry_id, inline_name, body in _split_sections(text):
        fields = _parse_section_fields(body)
        explicit_name = fields.get("Product Name", "").strip()
        entries.append(
            ManifestEntry(
                id=entry_id,
                product_name=explicit_name or inline_name,
                reference_image=fields.get("Reference Image", "").strip(),
                status=(fields.get("Status", "").strip() or STATUS_PENDING).lower(),
                image_prompt=fields.get("Image Prompt", "").strip(),
                video_prompt=fields.get("Video Prompt", "").strip(),
            )
        )
    return entries


def _detect_section_start(
    line: str, *, allow_numbered: bool
) -> tuple[str, str] | None:
    """Return (id, inline_name) if `line` looks like a section header, else None.

    Markdown headers (`# 01`, `## Product 01`, `### My Section 01: Name`) are
    always recognised. Numbered / dashed / parenthesised patterns are only
    recognised when `allow_numbered` is True — we suppress them while
    accumulating multi-line fields so a `1.` bullet inside an Image Prompt
    cannot be mistaken for a new section.
    """
    stripped = line.strip()
    if not stripped:
        return None

    m = _HEADER_RE.match(stripped)
    if m:
        return m.group(1), m.group(2).strip(" :-")

    if not allow_numbered:
        return None

    m = _NUMBERED_RE.match(stripped)
    if m:
        return m.group(1), m.group(2).strip(" :-")

    m = _BRACKETED_RE.match(stripped)
    if m:
        return m.group(1), m.group(2).strip(" :-")

    return None


def _split_sections(text: str) -> list[tuple[str, str, str]]:
    """Return [(id, inline_name, body), ...].

    Tracks whether we are currently inside an `Image Prompt` / `Video Prompt`
    accumulator so that bullet lines inside those prompts do not get treated
    as new sections.
    """
    out: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    in_multiline = False

    for raw_line in text.splitlines():
        detected = _detect_section_start(raw_line, allow_numbered=not in_multiline)
        if detected is not None:
            if current is not None:
                out.append(current)
            sid, inline_name = detected
            current = (sid, inline_name, [])
            in_multiline = False
            continue

        if current is None:
            continue

        current[2].append(raw_line)

        stripped = raw_line.strip()
        if stripped == "---":
            in_multiline = False
            continue
        m = _LABEL_RE.match(stripped)
        if m:
            label = m.group(1).strip()
            if label in _MULTI_LINE_LABELS:
                in_multiline = True
            elif label in _SINGLE_LINE_LABELS:
                in_multiline = False
        # else: continuation line → leave `in_multiline` as-is

    if current is not None:
        out.append(current)

    return [(sid, name, "\n".join(body)) for sid, name, body in out]


def _parse_section_fields(body: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current_label: str | None = None
    in_multiline = False

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "---":
            current_label = None
            in_multiline = False
            continue

        m = _LABEL_RE.match(stripped)
        label = m.group(1).strip() if m else None

        if label in _KNOWN_LABELS:
            value_part = m.group(2)
            current_label = label
            in_multiline = label in _MULTI_LINE_LABELS
            fields[label] = [value_part] if value_part.strip() else []
            continue

        # Continuation inside a multi-line field. Single-line labels do
        # not accumulate further lines.
        if current_label is not None and in_multiline:
            fields[current_label].append(line)

    return {k: "\n".join(v).strip() for k, v in fields.items()}


# ---------------------------------------------------------------------------
# --load-manifest
# ---------------------------------------------------------------------------


def run_load_manifest(
    settings: Settings,
    logger: logging.Logger,
    manifest_path: Path,
    *,
    fresh: bool = False,
) -> int:
    try:
        entries = parse_manifest(manifest_path)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return 1

    if not entries:
        logger.warning("Manifest %s contains no sections.", manifest_path)
        _dump_manifest_head(manifest_path, logger)
        return 1

    if fresh:
        if settings.products_csv.exists():
            from datetime import datetime
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = settings.products_csv.with_name(
                f"{settings.products_csv.stem}.csv.bak.{stamp}"
            )
            settings.products_csv.replace(backup)
            logger.info(
                "--fresh: backed up existing CSV to %s and starting empty.",
                backup,
            )
        rows: list[CsvRow] = []
        rows_by_id: dict[str, int] = {}
    else:
        rows = load_csv(settings.products_csv)
        rows_by_id = {r.id: i for i, r in enumerate(rows) if r.id}

    added = 0
    updated = 0
    skipped: list[tuple[str, str]] = []

    for entry in entries:
        if not entry.image_prompt:
            skipped.append((entry.id, "missing Image Prompt"))
            continue
        ref_path = resolve_reference_image(entry.reference_image, settings, logger)
        if not ref_path or not ref_path.exists():
            skipped.append((entry.id, f"reference image not found: {entry.reference_image!r}"))
            continue

        rel_image = ref_path.relative_to(settings.repo_root).as_posix()
        existing_idx = rows_by_id.get(entry.id)

        if existing_idx is None:
            rows.append(
                CsvRow(
                    id=entry.id,
                    product_name=entry.product_name,
                    image_path=rel_image,
                    image_prompt=entry.image_prompt,
                    video_prompt=entry.video_prompt,
                    status=entry.status or STATUS_PENDING,
                )
            )
            rows_by_id[entry.id] = len(rows) - 1
            added += 1
        else:
            row = rows[existing_idx]
            row.product_name = entry.product_name or row.product_name
            row.image_path = rel_image
            row.image_prompt = entry.image_prompt
            row.video_prompt = entry.video_prompt
            # Preserve any non-pending status the operator has already set
            # (e.g. image_submitted). Only seed status from the manifest if
            # the CSV row is still at pending or empty.
            if (row.status or STATUS_PENDING) == STATUS_PENDING and entry.status:
                row.status = entry.status
            updated += 1

    save_csv(settings.products_csv, rows)

    logger.info(
        "Manifest %s: %d added, %d updated, %d skipped",
        manifest_path,
        added,
        updated,
        len(skipped),
    )
    for entry_id, reason in skipped:
        logger.warning("  skipped id=%s: %s", entry_id, reason)
    return 0 if not skipped else 1


IMAGE_EXTENSIONS_REF: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")


def resolve_reference_image(
    value: str,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> Path | None:
    """Resolve a manifest 'Reference Image' value to a filesystem path.

    Lookup is fully flexible about extensions:

      1. Empty / missing  →  None.
      2. Absolute path     →  returned as-is.
      3. Contains '/' or '\\'  →  resolved against repo root.
      4. Bare name WITH extension (``01.png``)  →  first try a
         case-insensitive exact-name match in ``inputs/reference_images/``.
         If nothing matches, fall through to step 5 with the extension
         treated as a hint.
      5. Bare name WITHOUT extension (``01``)  →  scan
         ``inputs/reference_images/`` for any file whose stem matches
         case-insensitively and whose suffix is one of .jpg, .jpeg,
         .png, .webp. On multiple matches, prefer the suffix the
         manifest hinted at; otherwise warn and pick the first
         alphabetically.
      6. Nothing found  →  return a constructed (non-existent) path so
         the caller can show "expected at <path>".
    """
    if not value:
        return None

    p = Path(value)
    if p.is_absolute():
        return p
    if "/" in value or "\\" in value:
        return settings.repo_root / p

    target_dir = settings.reference_images_dir
    if not target_dir.exists():
        return target_dir / value

    user_ext = p.suffix
    user_stem = p.stem

    # 4. Exact-name match (case-insensitive) when an extension was given.
    if user_ext:
        name_lower = value.lower()
        for entry in sorted(target_dir.iterdir(), key=lambda e: e.name.lower()):
            if entry.is_file() and entry.name.lower() == name_lower:
                return entry
        # No exact match — fall through to stem search rather than
        # giving up. The manifest's extension becomes a hint at step 5.

    # 5. Stem-only search across supported extensions.
    matches: list[Path] = []
    stem_lower = user_stem.lower()
    for entry in target_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.stem.lower() != stem_lower:
            continue
        if entry.suffix.lower() not in IMAGE_EXTENSIONS_REF:
            continue
        matches.append(entry)

    # 6. Not found anywhere — return a constructed path.
    if not matches:
        ext = user_ext or IMAGE_EXTENSIONS_REF[0]
        return target_dir / f"{user_stem}{ext}"

    if len(matches) == 1:
        return matches[0]

    # Multiple stem matches: prefer the suffix the manifest hinted at.
    if user_ext:
        preferred = [m for m in matches if m.suffix.lower() == user_ext.lower()]
        if preferred:
            return preferred[0]

    matches.sort(key=lambda x: x.name.lower())
    if logger is not None:
        logger.warning(
            "Multiple reference images match %r: %s. Using %s.",
            value,
            ", ".join(m.name for m in matches),
            matches[0].name,
        )
    return matches[0]


# Back-compat alias for callers still using the private name.
_resolve_reference_image = resolve_reference_image


def _dump_manifest_head(path: Path, logger: logging.Logger, n: int = 20) -> None:
    """Print the first `n` lines of the manifest so a misformatted file is
    easy to spot. Called when `parse_manifest` returns no sections."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read manifest for diagnostic: %s", exc)
        return
    lines = text.splitlines()
    logger.info("First %d line(s) of %s:", min(n, len(lines)), path)
    for i, line in enumerate(lines[:n], start=1):
        logger.info("  %3d | %s", i, line)
    logger.info(
        "Section starts must contain a 1-3 digit ID. Accepted forms include "
        "'## 01', '### Product 01', '# Item 01', '1. Foo', '01 - Foo', "
        "'1) Foo', '[01]', '(01)'."
    )


# ---------------------------------------------------------------------------
# --validate-manifest
# ---------------------------------------------------------------------------


def run_validate_manifest(
    settings: Settings, logger: logging.Logger, manifest_path: Path
) -> int:
    try:
        entries = parse_manifest(manifest_path)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return 1

    print(f"Manifest: {manifest_path}")
    print(f"Sections found: {len(entries)}")

    if not entries:
        _dump_manifest_head(manifest_path, logger)
        return 1

    print()
    any_issues = False
    seen_ids: dict[str, int] = {}
    for entry in entries:
        seen_ids[entry.id] = seen_ids.get(entry.id, 0) + 1

        missing: list[str] = []
        if not entry.product_name:
            missing.append("Product Name")
        if not entry.reference_image:
            missing.append("Reference Image")
        if not entry.image_prompt:
            missing.append("Image Prompt")

        ref_resolved: Path | None = (
            resolve_reference_image(entry.reference_image, settings, logger)
            if entry.reference_image
            else None
        )
        ref_exists = bool(ref_resolved and ref_resolved.exists())

        ok = not missing and (not entry.reference_image or ref_exists)
        marker = "OK  " if ok else "WARN"
        print(f"[{marker}] id={entry.id}")
        print(f"    Product Name:    {entry.product_name or '(missing)'}")

        if entry.reference_image:
            print(f"    Reference Image: {entry.reference_image}")
            if ref_resolved:
                print(f"    Resolved Image:  {_display_path(ref_resolved, settings)}")
            print(f"    File Exists:     {ref_exists}")
        else:
            print("    Reference Image: (missing)")

        print(f"    Status:          {entry.status}")
        if entry.image_prompt:
            print(f"    Image Prompt:    {len(entry.image_prompt)} chars")
        else:
            print("    Image Prompt:    (missing)")
        if entry.video_prompt:
            print(f"    Video Prompt:    {len(entry.video_prompt)} chars")
        else:
            print("    Video Prompt:    (none)")

        if missing:
            print(f"    Missing Fields:  {', '.join(missing)}")
            any_issues = True
        if entry.reference_image and not ref_exists:
            print("    NOTE:            reference image file does not exist")
            any_issues = True
        print()

    duplicates = [sid for sid, n in seen_ids.items() if n > 1]
    if duplicates:
        print(f"Duplicate IDs: {', '.join(sorted(duplicates))}")
        any_issues = True

    return 1 if any_issues else 0


def _display_path(p: Path, settings: Settings) -> str:
    try:
        return p.relative_to(settings.repo_root).as_posix()
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# --generate-images-from-manifest
# ---------------------------------------------------------------------------


def run_generate_videos_from_manifest(
    settings: Settings, logger: logging.Logger, limit: int | None = None
) -> int:
    """Animate every row that has status=image_approved + video_prompt + flow_media_id.

    Marks each successful row video_submitted, atomically rewriting the
    CSV after every iteration. Errors are appended to the row's `notes`
    and the row's status is left at image_approved so it can be retried.
    """
    if not settings.products_csv.exists():
        logger.error("No %s yet. Load a manifest first.", settings.products_csv)
        return 1

    rows = load_csv(settings.products_csv)
    # Strict blanket-prompt mode (default): a row is ready as long as
    # the image is approved and a media_id exists. The video_prompt
    # column is ignored at the gate AND at send time. The per-product
    # video_prompt stays in the CSV for future advanced use.
    if settings.use_blanket_video_prompt:
        ready = [
            i for i, r in enumerate(rows)
            if r.status == STATUS_IMAGE_APPROVED and media_ids_of(r)
        ]
        if not ready:
            logger.info(
                "No rows ready for video (need status=image_approved + flow_media_id)."
            )
            return 0
    else:
        ready = [
            i for i, r in enumerate(rows)
            if r.status == STATUS_IMAGE_APPROVED
            and r.video_prompt
            and media_ids_of(r)
        ]
        if not ready:
            logger.info(
                "No rows ready for video (need status=image_approved AND "
                "video_prompt AND flow_media_id)."
            )
            return 0

    if limit is not None:
        ready = ready[:limit]

    import time
    logger.info(
        "Animating %d row(s) (mode=%s, retries=%d).",
        len(ready), settings.automation_mode, settings.video_retry_count,
    )
    batch_started = time.monotonic()
    successes = 0
    failures = 0
    per_product_seconds: list[float] = []
    with open_flow_browser(settings, logger) as session:
        page = acquire_flow_page(session, settings, logger)
        for n, idx in enumerate(ready, start=1):
            product_started = time.monotonic()
            row = rows[idx]
            # A row may have several captured variants; the user hearted
            # one of them. Default to the first; would be improved by
            # also recording which media_id was actually favorited.
            target_media_id = primary_media_id(row)
            logger.info(
                "--- [%d/%d] id=%s %s (media_id=%s)",
                n, len(ready), row.id or "-", row.product_name, target_media_id,
            )
            # Strict blanket-prompt mode: every product gets the same
            # universal video prompt. Avoids mismatches when the user
            # regenerates or manually favorites a different variant
            # than the one the row's video_prompt was authored against.
            if settings.use_blanket_video_prompt:
                video_prompt_to_send = settings.blanket_video_prompt
                logger.info(
                    "Using blanket video prompt for product %s",
                    row.id or row.product_name or "-",
                )
            else:
                video_prompt_to_send = row.video_prompt
            try:
                perform_recorded_video_flow(
                    page,
                    target_media_id,
                    video_prompt_to_send,
                    logger=logger,
                    selector_timeout_ms=settings.selector_timeout_ms,
                    tile_settle_ms=settings.video_tile_settle_ms,
                    after_hover_ms=settings.video_after_hover_ms,
                    after_menu_click_ms=settings.video_after_menu_click_ms,
                    retry_count=settings.video_retry_count,
                    logs_dir=settings.logs_dir,
                    debug_screenshots=settings.debug_screenshots,
                )
            except RecordedFlowError as exc:
                failures += 1
                logger.error(
                    "Video failed for %s: %s — marking video_error, continuing batch.",
                    row.product_name, exc,
                )
                rows[idx].status = STATUS_VIDEO_ERROR
                rows[idx].notes = _append_video_note(rows[idx].notes, f"video err: {exc}")
                save_csv(settings.products_csv, rows)
                per_product_seconds.append(time.monotonic() - product_started)
                # Inter-product cool-down even on failure so the browser
                # has time to settle before the next tile we touch.
                page.wait_for_timeout(settings.video_between_products_ms)
                continue
            except FlowAutomationError as exc:
                failures += 1
                logger.error(
                    "Flow error for %s: %s — marking video_error, continuing batch.",
                    row.product_name, exc,
                )
                rows[idx].status = STATUS_VIDEO_ERROR
                rows[idx].notes = _append_video_note(rows[idx].notes, f"video err: {exc}")
                save_csv(settings.products_csv, rows)
                page.wait_for_timeout(settings.video_between_products_ms)
                continue
            except Exception:  # noqa: BLE001
                failures += 1
                logger.exception(
                    "Unexpected error for %s — marking video_error, continuing batch.",
                    row.product_name,
                )
                rows[idx].status = STATUS_VIDEO_ERROR
                rows[idx].notes = _append_video_note(rows[idx].notes, "video err: unexpected")
                save_csv(settings.products_csv, rows)
                page.wait_for_timeout(settings.video_between_products_ms)
                continue

            rows[idx].status = STATUS_VIDEO_SUBMITTED
            save_csv(settings.products_csv, rows)
            successes += 1
            elapsed = time.monotonic() - product_started
            per_product_seconds.append(elapsed)
            logger.info(
                "Marked id=%s = %s (took %.1fs)",
                row.id or "-", STATUS_VIDEO_SUBMITTED, elapsed,
            )
            # Cool-down between successful submissions so the next tile
            # isn't touched while Flow is still committing the last one.
            page.wait_for_timeout(settings.video_between_products_ms)

    total_elapsed = time.monotonic() - batch_started
    avg = sum(per_product_seconds) / len(per_product_seconds) if per_product_seconds else 0.0
    logger.info(
        "Video batch done in %.1fs — %d submitted, %d failed, avg %.1fs/product.",
        total_elapsed, successes, failures, avg,
    )
    return 1 if failures else 0


def run_generate_videos_from_favorited_tiles(
    settings: Settings,
    logger: logging.Logger,
    limit: int | None = None,
    *,
    include_already_submitted: bool = False,
) -> int:
    """Animate every favorited image tile currently visible in Flow.

    This is the default code path for ``--generate-videos``. It does
    NOT require:
      - a row in products.csv,
      - status=image_approved,
      - a product-bound video_prompt,
      - unmatched-favorite binding.

    Behavior:
      1. Open Flow.
      2. Scan tiles, filter to favorited image tiles (skip video tiles
         and non-favorited tiles).
      3. De-dup by media_id.
      4. Skip media_ids previously submitted (per
         data/video_submitted_tiles.json) unless
         ``include_already_submitted`` is True.
      5. For each, animate with the blanket video prompt.

    Always uses the blanket prompt — the universal-prompt rule
    applies here too. The per-row video_prompt is irrelevant in this
    mode (there might not even be a row).
    """
    from .flow_tiles import scan_tiles
    from .video_state import load_submitted_media_ids, mark_submitted

    blanket = settings.blanket_video_prompt.strip()
    if not blanket:
        logger.error("BLANKET_VIDEO_PROMPT is empty — refusing to submit.")
        return 1

    import time
    batch_started = time.monotonic()
    successes = 0
    failures = 0
    skipped = 0
    per_tile_seconds: list[float] = []

    already = set() if include_already_submitted else load_submitted_media_ids()
    if include_already_submitted:
        logger.info("Include-already-submitted mode: ignoring prior submission history.")

    with open_flow_browser(settings, logger) as session:
        page = acquire_flow_page(session, settings, logger)

        tiles = scan_tiles(page, logger=logger)
        favorited = [
            t for t in tiles
            if t.favorited and t.flow_media_id and t.kind in {"image", ""}
        ]
        # De-dup by media_id; Flow's grid occasionally renders the same
        # tile twice (drag wrapper + inner card), and `scan_tiles`
        # already dedupes by tile_id, but media_id is the true key for
        # video submission so we apply a second dedupe pass here too.
        seen_media_ids: set[str] = set()
        ordered: list = []
        for t in favorited:
            if t.flow_media_id in seen_media_ids:
                continue
            seen_media_ids.add(t.flow_media_id)
            ordered.append(t)

        logger.info(
            "Scanned %d tile(s); %d favorited image tile(s) eligible.",
            len(tiles), len(ordered),
        )

        # Filter out the previously-submitted ones.
        to_submit = []
        for t in ordered:
            if t.flow_media_id in already:
                skipped += 1
                logger.info(
                    "Skipping media_id=%s (already submitted in a prior run).",
                    t.flow_media_id,
                )
                continue
            to_submit.append(t)

        if not to_submit:
            logger.info(
                "Nothing to submit — %d favorited tile(s) found, %d already submitted.",
                len(ordered), skipped,
            )
            return 0

        if limit is not None:
            to_submit = to_submit[:limit]

        logger.info(
            "Animating %d favorited tile(s) (mode=%s, retries=%d).",
            len(to_submit), settings.automation_mode, settings.video_retry_count,
        )

        for n, tile in enumerate(to_submit, start=1):
            tile_started = time.monotonic()
            logger.info(
                "--- [%d/%d] media_id=%s tile_id=%s edit_id=%s",
                n, len(to_submit), tile.flow_media_id,
                tile.flow_tile_id or "-", tile.edit_id or "-",
            )
            logger.info("Using blanket video prompt for media_id=%s", tile.flow_media_id)

            try:
                perform_recorded_video_flow(
                    page,
                    tile.flow_media_id,
                    blanket,
                    logger=logger,
                    selector_timeout_ms=settings.selector_timeout_ms,
                    tile_settle_ms=settings.video_tile_settle_ms,
                    after_hover_ms=settings.video_after_hover_ms,
                    after_menu_click_ms=settings.video_after_menu_click_ms,
                    retry_count=settings.video_retry_count,
                    logs_dir=settings.logs_dir,
                    debug_screenshots=settings.debug_screenshots,
                )
            except RecordedFlowError as exc:
                failures += 1
                logger.error(
                    "Video failed for media_id=%s: %s — continuing batch.",
                    tile.flow_media_id, exc,
                )
                page.wait_for_timeout(settings.video_between_products_ms)
                continue
            except FlowAutomationError as exc:
                failures += 1
                logger.error(
                    "Flow error for media_id=%s: %s — continuing batch.",
                    tile.flow_media_id, exc,
                )
                page.wait_for_timeout(settings.video_between_products_ms)
                continue
            except Exception:  # noqa: BLE001
                failures += 1
                logger.exception(
                    "Unexpected error for media_id=%s — continuing batch.",
                    tile.flow_media_id,
                )
                page.wait_for_timeout(settings.video_between_products_ms)
                continue

            successes += 1
            mark_submitted(tile.flow_media_id)
            elapsed = time.monotonic() - tile_started
            per_tile_seconds.append(elapsed)
            logger.info(
                "Video submitted for media_id=%s (took %.1fs)",
                tile.flow_media_id, elapsed,
            )
            page.wait_for_timeout(settings.video_between_products_ms)

    total_elapsed = time.monotonic() - batch_started
    avg = sum(per_tile_seconds) / len(per_tile_seconds) if per_tile_seconds else 0.0
    logger.info(
        "Favorited-tile video batch done in %.1fs — %d submitted, "
        "%d failed, %d skipped, avg %.1fs/tile.",
        total_elapsed, successes, failures, skipped, avg,
    )
    return 1 if failures else 0


def _append_video_note(existing: str, new: str) -> str:
    return new if not existing else f"{existing} | {new}"


def run_generate_images_from_manifest(
    settings: Settings, logger: logging.Logger, limit: int | None = None
) -> int:
    if not settings.products_csv.exists():
        logger.error(
            "No %s yet. Run --load-manifest first.", settings.products_csv
        )
        return 1

    rows = load_csv(settings.products_csv)
    pending = [
        i for i, r in enumerate(rows)
        if r.status == STATUS_PENDING and r.image_prompt
    ]
    if not pending:
        logger.info("No pending manifest-backed rows (status=pending with image_prompt set).")
        return 0

    if limit is not None:
        pending = pending[:limit]

    return process_rows(settings, logger, rows, pending)
