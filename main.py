"""CLI entry point for flow-bof-automation."""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from src.action_recorder import run_record_actions
from src.batch_runner import BatchOptions, run_batch
from src.config import load_settings
from src.csv_workflow import (
    run_generate_images,
    run_list_status,
    run_scan_images,
)
from src.flow_automation import run_check_browser, run_setup_browser
from src.manifest_workflow import (
    run_generate_images_from_manifest,
    run_generate_videos_from_manifest,
    run_load_manifest,
    run_validate_manifest,
)
from src.selector_debugger import run_debug_selectors
from src.sync_workflow import (
    run_bind_favorite,
    run_capture_tiles,
    run_list_unmatched_favorites,
    run_sync_favorites,
)
from src.user_settings import apply_to_env as apply_user_settings_to_env
from src.utils import ensure_dirs, setup_logging

# Push UI-saved settings/secrets into os.environ before load_settings()
# and the AI providers read it. Non-empty values override env so the UI
# is the source of truth when the user has saved one.
apply_user_settings_to_env()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flow Labs BOF automation")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts and validate inputs without opening Flow Labs.",
    )
    group.add_argument(
        "--setup-browser",
        action="store_true",
        help=(
            "One-time browser setup. In remote_debugging mode prints the Chrome "
            "launch command and verifies CDP. In persistent_profile mode opens "
            "Playwright's Chromium for manual login."
        ),
    )
    group.add_argument(
        "--check-browser",
        action="store_true",
        help=(
            "Connect to Chrome over CDP and report the browser version and open "
            "pages. Confirms Flow Labs is reachable. (remote_debugging mode only.)"
        ),
    )
    group.add_argument(
        "--debug-selectors",
        action="store_true",
        help=(
            "Inspect the live Flow Labs page and dump every interactive element "
            "to outputs/logs/selector_report_<ts>.json plus a full-page "
            "screenshot. Read-only — does not click or type."
        ),
    )
    group.add_argument(
        "--record-actions",
        action="store_true",
        help=(
            "Attach to your live Chrome, inject listeners into the Flow Labs "
            "page, and log every click / input / paste / file-change you "
            "perform. Saves outputs/logs/action_recording_<ts>.json + a final "
            "screenshot. Press Enter in the terminal to stop. Use this to "
            "capture real locators from your authenticated session, then "
            "paste them into src/recorded_flow.py."
        ),
    )
    group.add_argument(
        "--run-one",
        action="store_true",
        help=(
            "Run one full end-to-end generation using the recorded flow in "
            "src/recorded_flow.py. Defaults to product index 0; combine with "
            "--product-index N to pick a different product. Verifies that "
            "the result image was actually saved before returning success."
        ),
    )
    group.add_argument(
        "--scan-images",
        action="store_true",
        help=(
            "Scan inputs/incoming_images/ for new .jpg/.jpeg/.png/.webp "
            "files and append one row per new image to inputs/products.csv. "
            "product_name is derived from the filename; category defaults "
            "to 'auto' (one universal BOF prompt is used regardless)."
        ),
    )
    group.add_argument(
        "--list-status",
        action="store_true",
        help=(
            "Print a status histogram for inputs/products.csv and list the "
            "pending rows."
        ),
    )
    group.add_argument(
        "--generate-images",
        action="store_true",
        help=(
            "Process pending rows in inputs/products.csv: upload reference "
            "image, attach to prompt, paste the BOF prompt, click generate. "
            "Marks status=image_submitted after each row and rewrites the "
            "CSV. Use --limit N to cap the batch."
        ),
    )
    group.add_argument(
        "--load-manifest",
        nargs="?",
        const="inputs/prompt_manifest.md",
        default=None,
        metavar="PATH",
        help=(
            "Parse a Markdown prompt manifest and create/update "
            "inputs/products.csv. Each `## NN` section must include "
            "Product Name, Reference Image, Status, Image Prompt, and "
            "(optionally) Video Prompt. Reference images are resolved "
            "from inputs/reference_images/ by default. Defaults to "
            "inputs/prompt_manifest.md when no PATH is given."
        ),
    )
    group.add_argument(
        "--generate-images-from-manifest",
        action="store_true",
        help=(
            "Process pending rows whose image_prompt is set (manifest-"
            "backed). Sends the exact image_prompt to Flow Labs; no "
            "category/store guessing. Use --limit N to cap the batch."
        ),
    )
    group.add_argument(
        "--validate-manifest",
        nargs="?",
        const="inputs/prompt_manifest.md",
        default=None,
        metavar="PATH",
        help=(
            "Parse a manifest and print all detected sections, IDs, "
            "product names, reference image paths, and any missing "
            "fields. Read-only — does not touch products.csv. Defaults "
            "to inputs/prompt_manifest.md when no PATH is given."
        ),
    )
    group.add_argument(
        "--capture-tiles",
        action="store_true",
        help=(
            "Scan the live Flow Labs page for generated-image tiles "
            "(elements with data-tile-id) and bind their flow_tile_id / "
            "flow_image_src / flow_media_id / tile_href onto image_"
            "submitted CSV rows that lack a flow_media_id, in submission "
            "order. Run after --generate-images-from-manifest."
        ),
    )
    group.add_argument(
        "--sync-favorites",
        action="store_true",
        help=(
            "Scan the live Flow Labs page for favorited tiles and flip "
            "matching CSV rows to status=image_approved. Matches by "
            "flow_media_id; run --capture-tiles first if rows do not "
            "have one yet."
        ),
    )
    group.add_argument(
        "--list-unmatched-favorites",
        action="store_true",
        help=(
            "Print the contents of data/unmatched_favorites.json — "
            "favorited Flow tiles whose media_id didn't match any CSV "
            "row (typically a manually regenerated variant)."
        ),
    )
    group.add_argument(
        "--bind-favorite",
        action="store_true",
        help=(
            "Bind a single unmatched favorite to a CSV product by id. "
            "Requires --media-id and --product-id. Promotes the "
            "media_id to the front of the row's list and removes it "
            "from data/unmatched_favorites.json."
        ),
    )
    group.add_argument(
        "--generate-videos-from-manifest",
        action="store_true",
        help=(
            "Animate rows where status=image_approved AND video_prompt "
            "is set AND flow_media_id is captured. Hovers each tile, "
            "clicks its overflow > Animate, fills the video prompt, and "
            "clicks the arrow. Marks status=video_submitted."
        ),
    )
    group.add_argument(
        "--generate-videos",
        action="store_true",
        help=(
            "Canonical daily-flow alias for --generate-videos-from-manifest. "
            "Processes rows with status=image_approved AND video_prompt AND "
            "flow_media_id. Use --limit N to cap the batch."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N products from inputs/products.json.",
    )
    parser.add_argument(
        "--product-index",
        type=int,
        default=None,
        help="Process only the product at the given zero-based index.",
    )
    parser.add_argument(
        "--approve-only",
        action="store_true",
        help=(
            "With --sync-favorites: only flip favorited rows to "
            "image_approved; never downgrade or auto-reject. This is the "
            "current default — the flag is accepted explicitly so future "
            "sync modes (e.g. auto-reject of unfavorited) stay opt-in."
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "With --load-manifest: back up the existing inputs/products.csv "
            "and start with an empty CSV so only the current manifest's "
            "products are in it. Use this when starting a new batch."
        ),
    )
    parser.add_argument(
        "--media-id",
        type=str,
        default=None,
        help="Used with --bind-favorite: the favorited tile's media_id.",
    )
    parser.add_argument(
        "--product-id",
        type=str,
        default=None,
        help="Used with --bind-favorite: the CSV row's product id.",
    )
    parser.add_argument(
        "--no-auto-bind",
        action="store_true",
        help=(
            "With --sync-favorites: do NOT auto-bind unmatched favorited "
            "tiles to unmatched image_submitted rows in submission order. "
            "Auto-bind is on by default and only triggers when the number "
            "of unmatched favorited tiles equals the number of unmatched "
            "rows."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    ensure_dirs(settings)
    logger = setup_logging(settings)

    if args.setup_browser:
        return run_setup_browser(settings, logger)

    if args.check_browser:
        return run_check_browser(settings, logger)

    if args.debug_selectors:
        return run_debug_selectors(settings, logger)

    if args.record_actions:
        return run_record_actions(settings, logger)

    if args.scan_images:
        return run_scan_images(settings, logger)

    if args.list_status:
        return run_list_status(settings, logger)

    if args.generate_images:
        return run_generate_images(settings, logger, limit=args.limit)

    if args.load_manifest is not None:
        return run_load_manifest(
            settings, logger, Path(args.load_manifest), fresh=args.fresh
        )

    if args.generate_images_from_manifest:
        return run_generate_images_from_manifest(settings, logger, limit=args.limit)

    if args.validate_manifest is not None:
        return run_validate_manifest(settings, logger, Path(args.validate_manifest))

    if args.capture_tiles:
        return run_capture_tiles(settings, logger)

    if args.sync_favorites:
        # `--approve-only` is the only mode in v1; bare `--sync-favorites`
        # also does approve-only. The flag is accepted explicitly so the
        # daily-flow doc can showcase the safer form.
        return run_sync_favorites(
            settings,
            logger,
            approve_only=True,
            auto_bind_unmatched=not args.no_auto_bind,
        )

    if args.list_unmatched_favorites:
        return run_list_unmatched_favorites(settings, logger)

    if args.bind_favorite:
        if not args.media_id or not args.product_id:
            logger.error("--bind-favorite requires --media-id and --product-id.")
            return 1
        return run_bind_favorite(settings, logger, args.media_id, args.product_id)

    if args.generate_videos_from_manifest or args.generate_videos:
        return run_generate_videos_from_manifest(settings, logger, limit=args.limit)

    if args.run_one:
        product_index = args.product_index if args.product_index is not None else 0
        opts = BatchOptions(product_index=product_index)
        return run_batch(settings, logger, opts)

    opts = BatchOptions(
        dry_run=args.dry_run,
        limit=args.limit,
        product_index=args.product_index,
    )
    return run_batch(settings, logger, opts)


if __name__ == "__main__":
    sys.exit(main())
