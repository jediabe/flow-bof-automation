"""Phase-1 local agent job interface.

A single entry point — :func:`handle_agent_job` — that any future SaaS
dispatcher (HTTP service, WebSocket consumer, etc.) can call to run a
typed job against this machine's local automation.

This file deliberately has a tiny surface for Phase 1: one dispatcher,
one job type (``health_check``), and a fixed JSON envelope shape. New
job types lift one at a time into ``_JOB_HANDLERS`` as we work down
the list in ``docs/JOB_PROTOCOL.md``.

Envelope shape — see ``docs/JOB_PROTOCOL.md`` for the long form. Short
form for the local-only Phase 1::

    Input
        {"protocol_version": "0.1",
         "job_id":           "<id>",
         "job_type":         "health_check",
         "payload":          {}}

    Output (success)
        {"protocol_version": "0.1",
         "job_id":           "<id>",
         "job_type":         "health_check",
         "status":           "succeeded",
         "result":           {...},
         "error":            None}

    Output (failure)
        {"protocol_version": "0.1",
         "job_id":           "<id>",
         "job_type":         "health_check",
         "status":           "failed",
         "result":           None,
         "error":            {"code":    "...",
                              "message": "...",
                              "details": {}}}

The handler never raises and never writes to stdout. The caller (today
``main.py --agent-job ...``; tomorrow an HTTP service) is responsible
for serializing the dict and choosing the exit code.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import platform
import random
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse


# Type alias: a progress callback receives one fully-formed progress
# event dict and may do anything with it (print, ship to a WebSocket,
# discard). Returns None. Must not raise — the emitter wraps the call
# in a try/except so a busted callback never kills a running job.
ProgressCallback = Callable[[dict], None]


PROTOCOL_VERSION = "0.1"

# Bumped manually when the agent's wire behavior changes. Surfaced in
# health_check results so the dashboard can flag agents that need an
# update.
APP_VERSION = "0.6.3-alpha"


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def _success(job: dict, result: dict) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "job_id":           job.get("job_id", ""),
        "job_type":         job.get("job_type", ""),
        "status":           "succeeded",
        "result":           result,
        "error":            None,
    }


def _make_emitter(
    job: dict,
    progress_callback: Optional[ProgressCallback],
) -> Callable[..., None]:
    """Build a per-job ``emit(stage, message, *, current, total, details)``.

    If ``progress_callback`` is None, ``emit`` is a no-op — handlers can
    always call it without checking. If the callback raises, we swallow
    the exception: progress is informational, not load-bearing, and a
    crash in the dashboard/sender path must NEVER kill a running batch.
    """
    if progress_callback is None:
        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return
        return _noop

    job_id = job.get("job_id", "")
    job_type = job.get("job_type", "")

    def emit(
        stage: str,
        message: str = "",
        *,
        current: int | None = None,
        total: int | None = None,
        details: dict | None = None,
    ) -> None:
        evt = {
            "protocol_version": PROTOCOL_VERSION,
            "job_id":           job_id,
            "job_type":         job_type,
            "event_type":       "progress",
            "stage":            stage,
            "message":          message,
            "current":          current,
            "total":            total,
            "details":          details or {},
        }
        try:
            progress_callback(evt)
        except Exception:  # noqa: BLE001
            pass

    return emit


def _failure(
    job: dict,
    code: str,
    message: str,
    details: dict | None = None,
) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "job_id":           job.get("job_id", ""),
        "job_type":         job.get("job_type", ""),
        "status":           "failed",
        "result":           None,
        "error": {
            "code":    code,
            "message": message,
            "details": details or {},
        },
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_health_check(
    job: dict,
    logger: logging.Logger,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Lightweight diagnostic.

    Guarantees, by design:
      * Never launches Chrome or Playwright.
      * Never mutates state.
      * Never requires Flow login.
      * Never raises out of this function — every probe failure is
        surfaced as a structured ``False`` flag in the result, not as
        a job-level failure.

    Reports:
      * ``agent_ok`` — True iff the handler ran to completion. (Going
        ``False`` is reserved for a future genuine self-test step.)
      * ``app_version``, ``python_version``, ``platform`` — static
        environment fingerprint.
      * ``chrome_cdp_url`` — what the agent would try to connect to.
      * ``chrome_reachable``, ``flow_reachable`` — best-effort HTTP
        HEAD probes. False if Chrome / Flow isn't up, with the failure
        reason in the sibling ``*_probe_note``.
    """
    # Imports here, not at module top, so a broken setting doesn't keep
    # the agent_api module itself from loading. The health_check job is
    # supposed to *describe* a broken environment, not be killed by it.
    chrome_cdp_url = ""
    try:
        from .config import load_settings
        chrome_cdp_url = load_settings().chrome_cdp_url
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent health_check: load_settings failed: %s", exc)

    chrome_reachable, chrome_note = False, "not probed"
    flow_reachable, flow_note = False, "not probed"
    try:
        from .health import (
            check_chrome_debug_reachable,
            check_flow_labs_reachable,
        )
        try:
            status, msg = check_chrome_debug_reachable()
            chrome_reachable = (status == "ok")
            chrome_note = msg
        except Exception as exc:  # noqa: BLE001
            chrome_note = f"probe error: {type(exc).__name__}: {exc}"
        try:
            status, msg = check_flow_labs_reachable()
            flow_reachable = (status == "ok")
            flow_note = msg
        except Exception as exc:  # noqa: BLE001
            flow_note = f"probe error: {type(exc).__name__}: {exc}"
    except ImportError as exc:
        chrome_note = flow_note = f"src.health unavailable: {exc}"

    return _success(job, {
        "agent_ok":          True,
        "app_version":       APP_VERSION,
        "python_version":    sys.version.split()[0],
        "platform":          (
            f"{platform.system().lower()}-{platform.machine().lower()}"
        ),
        "chrome_cdp_url":    chrome_cdp_url,
        "chrome_reachable":  chrome_reachable,
        "chrome_probe_note": chrome_note,
        "flow_reachable":    flow_reachable,
        "flow_probe_note":   flow_note,
    })


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _find_existing_flow_page(session, settings):
    """Return the first already-open Flow tab in the session, or None.

    We deliberately do NOT call the existing acquire_flow_page() helper
    because that function navigates to Flow if no tab exists — a side
    effect this read-only handler should not have. If the user closed
    their Flow tab, the scan job must fail with FLOW_PAGE_NOT_FOUND,
    not silently open a new window in their Chrome.
    """
    # Lazy import so a missing flow_automation module isn't fatal at
    # agent_api import time.
    from urllib.parse import urlparse
    from .config import load_settings as _ls

    settings_local = settings or _ls()
    target_netloc = ""
    try:
        target_netloc = urlparse(settings_local.flow_labs_url).netloc.lower()
    except Exception:  # noqa: BLE001
        target_netloc = ""

    if not target_netloc:
        return None

    pages = getattr(session.context, "pages", None) or []
    for page in pages:
        try:
            actual = urlparse(page.url).netloc.lower()
        except Exception:  # noqa: BLE001
            continue
        if actual == target_netloc:
            return page
    return None


def _tile_to_item(tile) -> dict:
    """Map a TileInfo dataclass into the JSON shape the spec defines."""
    return {
        "media_id":      tile.flow_media_id,
        "tile_id":       tile.flow_tile_id,
        "edit_id":       tile.edit_id,
        "tile_href":     tile.tile_href,
        # `kind` blank in the scanner means "couldn't tell" — we treat
        # those as image tiles for the dashboard's convenience. Video
        # tiles always carry kind="video" so the filter below still
        # excludes them by default.
        "kind":          tile.kind or "image",
        "favorited":     bool(tile.favorited),
        "thumbnail_src": tile.flow_image_src,
    }


def _handle_scan_favorited_images(
    job: dict,
    logger: logging.Logger,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Read-only scan of the Flow grid.

    Guarantees:
      * Never mutates products.csv, settings, batches, video state.
      * Never clicks Animate or generates videos.
      * Never opens a new Flow tab — if no Flow tab is currently open,
        returns FLOW_PAGE_NOT_FOUND.
      * Never logs in or navigates anywhere.

    Payload fields (all optional):
      * limit (int, default 100): cap on items returned.
      * include_non_favorites (bool, default False): if True, returns
        every tile including unfavorited ones.
      * include_videos (bool, default False): if True, returns video
        tiles alongside image tiles.

    Counts (`tiles_scanned`, `favorited_images_count`,
    `favorited_videos_count`) reflect the *full* scan, not the filtered
    list — so the dashboard can show "5 of 12 favorites" even when
    limit=5.
    """
    payload = job.get("payload") or {}

    # Validate + coerce payload, never raise on bad input.
    try:
        limit = int(payload.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    if limit <= 0:
        limit = 100
    include_non_favorites = bool(payload.get("include_non_favorites", False))
    include_videos = bool(payload.get("include_videos", False))

    # Lazy imports keep the agent_api module loadable even when
    # Playwright isn't installed (e.g. when only the JSON envelope
    # shape is being inspected).
    try:
        from .config import load_settings
        from .flow_automation import (
            FlowAutomationError,
            open_flow_browser,
        )
        from .flow_tiles import scan_tiles
    except ImportError as exc:
        return _failure(
            job,
            "AGENT_DEPENDENCY_MISSING",
            f"Cannot import flow modules: {exc}",
        )

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        return _failure(
            job,
            "BAD_AGENT_CONFIG",
            f"load_settings raised: {type(exc).__name__}: {exc}",
        )

    # Connect to Chrome, find an existing Flow tab, scan, disconnect.
    # The context manager is what disconnects from CDP — we never
    # close the user's actual Chrome.
    try:
        with open_flow_browser(settings, logger) as session:
            page = _find_existing_flow_page(session, settings)
            if page is None:
                return _failure(
                    job,
                    "FLOW_PAGE_NOT_FOUND",
                    (
                        f"No tab matching {settings.flow_labs_url} is open in "
                        "the connected Chrome. Open Flow Labs in the debug "
                        "Chrome window and try again."
                    ),
                )

            # Cheap reachability probe — page.url succeeding means the
            # page object is alive and the CDP session works. We don't
            # require Flow's UI to be fully rendered (the scanner has
            # its own DOM probes).
            try:
                current_url = page.url
            except Exception as exc:  # noqa: BLE001
                return _failure(
                    job,
                    "FLOW_NOT_REACHABLE",
                    f"Flow tab is open but unresponsive: "
                    f"{type(exc).__name__}: {exc}",
                )

            try:
                tiles = scan_tiles(page, logger=logger)
            except Exception as exc:  # noqa: BLE001
                return _failure(
                    job,
                    "FLOW_SCAN_FAILED",
                    f"scan_tiles raised: {type(exc).__name__}: {exc}",
                )

    except FlowAutomationError as exc:
        msg = str(exc)
        # FlowAutomationError covers both "couldn't connect to Chrome"
        # and "couldn't open Flow" — disambiguate by inspecting the
        # message. The CDP-connect path always includes the literal
        # CDP URL, which is the safest distinguisher.
        if (
            "Could not connect to Chrome" in msg
            or "connect_over_cdp" in msg
            or "9222" in msg
            or "9333" in msg
        ):
            return _failure(job, "CHROME_NOT_REACHABLE", msg)
        return _failure(job, "FLOW_NOT_REACHABLE", msg)
    except Exception as exc:  # noqa: BLE001
        # Outer safety net for anything Playwright-related the inner
        # blocks didn't already absorb (e.g. the CDP socket dropping
        # mid-context-manager). Still well-formed envelope out.
        return _failure(
            job,
            "SCAN_FAVORITED_IMAGES_FAILED",
            f"{type(exc).__name__}: {exc}",
        )

    # ---- Build the result. Counts use the FULL scan; items get filtered. ----
    favorited_images_count = sum(
        1 for t in tiles
        if t.favorited and (t.kind == "image" or t.kind == "")
    )
    favorited_videos_count = sum(
        1 for t in tiles
        if t.favorited and t.kind == "video"
    )

    filtered: list[dict] = []
    for t in tiles:
        if t.kind == "video" and not include_videos:
            continue
        if not t.favorited and not include_non_favorites:
            continue
        filtered.append(_tile_to_item(t))
        if len(filtered) >= limit:
            break

    return _success(job, {
        "chrome_reachable":       True,
        "flow_reachable":         True,
        "flow_url":               current_url,
        "tiles_scanned":          len(tiles),
        "favorited_images_count": favorited_images_count,
        "favorited_videos_count": favorited_videos_count,
        "items":                  filtered,
    })


def _infer_signed_in(page_url: str, tiles: list, notes: list[str]) -> bool | None:
    """Heuristic: is the user signed in to Google / Flow?

    Conservative — returns True only when we're confident, False when
    we have a clear sign-in redirect signal, and None when the signals
    are ambiguous (with a note explaining).
    """
    url = (page_url or "").lower()
    if not url:
        notes.append("page.url was empty; cannot infer signed_in_likely")
        return None

    # Hard NO: a sign-in / consent redirect.
    if (
        "accounts.google.com" in url
        or "signin" in url
        or "ServiceLogin" in (page_url or "")
        or "/auth" in url and "labs.google" not in url
    ):
        return False

    # Hard YES: we're on a labs.google/flow path. Google would have
    # redirected away from this URL on the server side if the user
    # weren't authenticated.
    if "labs.google" in url and ("/flow" in url or url.endswith("/flow")):
        return True

    # Soft YES: we found tiles. Anonymous Flow doesn't render tiles.
    if tiles:
        notes.append("signed_in_likely inferred from non-empty tile grid")
        return True

    # Ambiguous.
    notes.append(
        f"signed_in_likely could not be inferred from URL={page_url!r} "
        "and no tiles were visible"
    )
    return None


def _infer_project_open(page_url: str, tiles: list, notes: list[str]) -> bool | None:
    """Heuristic: is the user inside a Flow project (vs. the landing page)?

    Strong signals:
      - URL contains "/project/" or "/edit/".
      - The tile grid has any tiles in it.

    A landing page typically has no tiles and a URL of just
    https://labs.google/flow.
    """
    url = (page_url or "").lower()
    if "/project/" in url or "/edit/" in url:
        return True
    if tiles:
        # Tile grid only renders inside a project context.
        return True
    # No tiles and no project URL — most likely the user is at the
    # landing/new-project chooser. We don't try to detect that
    # explicitly; the absence-of-evidence is enough to say False here.
    return False


def _handle_check_flow_connection(
    job: dict,
    logger: logging.Logger,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Read-only probe of an existing Flow tab.

    Guarantees:
      * No new tabs opened.
      * No navigation.
      * No clicks, no input.
      * No CSV / state mutation.

    Returns dashboard-friendly readiness data: whether Flow is open,
    the URL, sign-in / project inference, and tile counts. If Chrome
    isn't running or no Flow tab is open, returns a structured failure
    rather than trying to fix it.
    """
    # Lazy imports — same rationale as scan_favorited_images: a missing
    # Playwright install shows up as a clean AGENT_DEPENDENCY_MISSING
    # instead of an uncaught ImportError.
    try:
        from .config import load_settings
        from .flow_automation import FlowAutomationError, open_flow_browser
        from .flow_tiles import scan_tiles
    except ImportError as exc:
        return _failure(
            job,
            "AGENT_DEPENDENCY_MISSING",
            f"Cannot import flow modules: {exc}",
        )

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        return _failure(
            job,
            "BAD_AGENT_CONFIG",
            f"load_settings raised: {type(exc).__name__}: {exc}",
        )

    notes: list[str] = []
    flow_url = ""
    tiles: list = []

    try:
        with open_flow_browser(settings, logger) as session:
            page = _find_existing_flow_page(session, settings)
            if page is None:
                # Don't open a new tab — spec says fail loudly.
                return _failure(
                    job,
                    "FLOW_PAGE_NOT_FOUND",
                    (
                        f"No tab matching {settings.flow_labs_url} is open in "
                        "the connected Chrome. Open Flow Labs in the debug "
                        "Chrome window and try again."
                    ),
                )

            try:
                flow_url = page.url
            except Exception as exc:  # noqa: BLE001
                return _failure(
                    job,
                    "FLOW_NOT_REACHABLE",
                    f"Flow tab is open but unresponsive: "
                    f"{type(exc).__name__}: {exc}",
                )

            # scan_tiles is read-only. Any failure here we report as a
            # note rather than failing the whole connection check — the
            # user might be inside a project where the grid is still
            # initializing.
            try:
                tiles = scan_tiles(page, logger=logger)
            except Exception as exc:  # noqa: BLE001
                notes.append(
                    f"scan_tiles raised {type(exc).__name__}: {exc} "
                    "(tile counts will be 0)"
                )
                tiles = []

    except FlowAutomationError as exc:
        msg = str(exc)
        if (
            "Could not connect to Chrome" in msg
            or "connect_over_cdp" in msg
            or "9222" in msg
            or "9333" in msg
        ):
            return _failure(job, "CHROME_NOT_REACHABLE", msg)
        return _failure(job, "FLOW_NOT_REACHABLE", msg)
    except Exception as exc:  # noqa: BLE001
        return _failure(
            job,
            "CHECK_FLOW_CONNECTION_FAILED",
            f"{type(exc).__name__}: {exc}",
        )

    # ---- Counts (full scan, not filtered) -------------------------------
    image_tile_count = sum(
        1 for t in tiles if t.kind == "image" or t.kind == ""
    )
    video_tile_count = sum(1 for t in tiles if t.kind == "video")
    favorited_image_count = sum(
        1 for t in tiles
        if t.favorited and (t.kind == "image" or t.kind == "")
    )

    # ---- Inferences -----------------------------------------------------
    signed_in_likely = _infer_signed_in(flow_url, tiles, notes)
    project_open_likely = _infer_project_open(flow_url, tiles, notes)

    return _success(job, {
        "chrome_reachable":      True,
        "flow_page_found":       True,
        "flow_url":              flow_url,
        # If we got this far the page is responsive. We report
        # flow_reachable=True even when no tiles render — being signed
        # out is a separate signal, surfaced via signed_in_likely.
        "flow_reachable":        True,
        "signed_in_likely":      signed_in_likely,
        "project_open_likely":   project_open_likely,
        "tile_count":            len(tiles),
        "image_tile_count":      image_tile_count,
        "video_tile_count":      video_tile_count,
        "favorited_image_count": favorited_image_count,
        "notes":                 notes,
    })


def _handle_generate_flow_videos_from_favorites(
    job: dict,
    logger: logging.Logger,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Animate every favorited image tile with the universal blanket
    video prompt. The first MUTATING agent handler — it clicks Animate
    and submits video generation per tile.

    Read-only invariants this handler still respects:
      * No products.csv reads or writes.
      * No product binding, no status flips, no manifest changes.
      * No new Flow tab opened (uses the existing one or fails fast
        with FLOW_PAGE_NOT_FOUND).
      * Does not skip a row because a CSV mapping is missing — the
        favorited media_id is authoritative.
      * No TikTok-side activity.

    Behavior:
      1. Scan Flow grid; filter favorited image tiles; dedup by media_id.
      2. Drop any media_id already in data/video_submitted_tiles.json
         (unless include_already_submitted=true).
      3. Cap to `limit` tiles.
      4. For each: call perform_recorded_video_flow() with the blanket
         prompt. mark_submitted() on success. On per-tile failure,
         record it in result.items and CONTINUE to the next tile.

    Status logic:
      * Whole-batch errors (Chrome down, scan failed, blanket prompt
        empty) → status=failed.
      * Per-tile errors → status=succeeded with non-zero `failed`
        count and per-item entries.

    The spec doesn't define a "succeeded_with_errors" status today, so
    we follow the rule "If only succeeded/failed are supported, return
    succeeded if the job ran and include failed count in result."
    """
    payload = job.get("payload") or {}
    emit = _make_emitter(job, progress_callback)
    started_at = time.monotonic()

    # ---- Coerce payload ------------------------------------------------
    try:
        limit = int(payload.get("limit") or 30)
    except (TypeError, ValueError):
        limit = 30
    if limit <= 0:
        limit = 30
    include_already_submitted = bool(
        payload.get("include_already_submitted", False)
    )
    blanket_override = payload.get("blanket_video_prompt")

    # ---- Lazy imports --------------------------------------------------
    try:
        from .config import DEFAULT_BLANKET_VIDEO_PROMPT, load_settings
        from .flow_automation import (
            FlowAutomationError,
            open_flow_browser,
        )
        from .flow_tiles import scan_tiles
        from .recorded_flow import (
            RecordedFlowError,
            perform_recorded_video_flow,
        )
        from .video_state import load_submitted_media_ids, mark_submitted
    except ImportError as exc:
        return _failure(
            job,
            "AGENT_DEPENDENCY_MISSING",
            f"Cannot import video-flow modules: {exc}",
        )

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        return _failure(
            job,
            "BAD_AGENT_CONFIG",
            f"load_settings raised: {type(exc).__name__}: {exc}",
        )

    # ---- Resolve blanket prompt ---------------------------------------
    if isinstance(blanket_override, str) and blanket_override.strip():
        blanket_prompt = blanket_override.strip()
        blanket_source = "payload_override"
    else:
        blanket_prompt = (
            settings.blanket_video_prompt or DEFAULT_BLANKET_VIDEO_PROMPT
        ).strip()
        blanket_source = "settings"
    if not blanket_prompt:
        return _failure(
            job,
            "BLANKET_PROMPT_EMPTY",
            "BLANKET_VIDEO_PROMPT is not set and no payload override "
            "provided. Refusing to submit blank prompts.",
        )

    # ---- Already-submitted set ----------------------------------------
    if include_already_submitted:
        already_submitted: set[str] = set()
    else:
        try:
            already_submitted = load_submitted_media_ids()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "load_submitted_media_ids failed (%s); proceeding as if empty.",
                exc,
            )
            already_submitted = set()

    # ---- Per-tile bookkeeping -----------------------------------------
    items: list[dict] = []
    favorited_images_found = 0
    submitted = 0
    failed = 0
    skipped = 0

    def _record_item(tile, status: str, err: dict | None = None) -> None:
        items.append({
            "media_id": tile.flow_media_id,
            "tile_id":  tile.flow_tile_id,
            "edit_id":  tile.edit_id,
            "status":   status,
            "error":    err,
        })

    # ---- Open browser, scan, iterate ----------------------------------
    emit("scanning_favorites", "Scanning favorited images...")
    try:
        with open_flow_browser(settings, logger) as session:
            page = _find_existing_flow_page(session, settings)
            if page is None:
                return _failure(
                    job,
                    "FLOW_PAGE_NOT_FOUND",
                    (
                        f"No tab matching {settings.flow_labs_url} is open. "
                        "Open Flow Labs in the debug Chrome window and try "
                        "again."
                    ),
                )

            try:
                tiles = scan_tiles(page, logger=logger)
            except Exception as exc:  # noqa: BLE001
                return _failure(
                    job,
                    "FLOW_SCAN_FAILED",
                    f"scan_tiles raised: {type(exc).__name__}: {exc}",
                )

            # Filter to favorited image tiles, dedup by media_id.
            # Same order as src.manifest_workflow.run_generate_videos_from_favorited_tiles
            # so behavior matches what the local Streamlit button does.
            seen_media: set[str] = set()
            favorited: list = []
            for t in tiles:
                if not t.favorited:
                    continue
                if t.kind == "video":
                    continue
                if not t.flow_media_id:
                    continue
                if t.flow_media_id in seen_media:
                    continue
                seen_media.add(t.flow_media_id)
                favorited.append(t)
            favorited_images_found = len(favorited)

            logger.info(
                "Scanned %d tile(s); %d favorited image tile(s) eligible.",
                len(tiles), favorited_images_found,
            )

            # Drop already-submitted; emit skipped items so the dashboard
            # can show why a tile didn't get re-animated.
            pending: list = []
            for t in favorited:
                if t.flow_media_id in already_submitted:
                    skipped += 1
                    _record_item(t, "skipped_already_submitted")
                    continue
                pending.append(t)

            # Apply limit.
            to_submit = pending[:limit]
            logger.info(
                "Animating %d favorited tile(s) (mode=%s, retries=%d, "
                "skipped=%d, blanket_source=%s).",
                len(to_submit), settings.automation_mode,
                settings.video_retry_count, skipped, blanket_source,
            )
            emit(
                "favorites_found",
                f"Found {favorited_images_found} favorited image(s); "
                f"{len(to_submit)} to process, {skipped} already submitted.",
                details={
                    "favorited_images_found":     favorited_images_found,
                    "skipped_already_submitted":  skipped,
                    "to_process":                 len(to_submit),
                },
            )

            # ---- Per-tile loop ----------------------------------------
            for n, tile in enumerate(to_submit, start=1):
                logger.info(
                    "--- [%d/%d] media_id=%s tile_id=%s edit_id=%s",
                    n, len(to_submit),
                    tile.flow_media_id,
                    tile.flow_tile_id or "-",
                    tile.edit_id or "-",
                )
                logger.info(
                    "Using blanket video prompt for media_id=%s",
                    tile.flow_media_id,
                )
                emit(
                    "processing_tile",
                    f"Submitting video {n} of {len(to_submit)}...",
                    current=n,
                    total=len(to_submit),
                    details={
                        "media_id": tile.flow_media_id,
                        "tile_id":  tile.flow_tile_id,
                        "edit_id":  tile.edit_id,
                    },
                )

                # Centralised per-tile Flow-UI cleanup: dismiss any
                # menu / dialog / agent pill left over from the
                # previous tile so the next overflow click lands on
                # the right element. perform_recorded_video_flow
                # also has its own Escape + mouse-to-corner at the
                # top; this layer adds the aria-label close-button
                # + Radix menu sweeps. Never raises.
                try:
                    from .flow_ui_prep import prepare_flow_for_video_generation
                    prep_report = prepare_flow_for_video_generation(
                        page, logger=logger,
                    )
                    if prep_report and not prep_report.get("skipped"):
                        emit(
                            "flow_ui_prep",
                            "Flow UI prep complete",
                            current=n,
                            total=len(to_submit),
                            details={"prep": prep_report},
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "flow-ui-prep raised before video submit "
                        "(media_id=%s): %s", tile.flow_media_id, exc,
                    )

                try:
                    perform_recorded_video_flow(
                        page,
                        tile.flow_media_id,
                        blanket_prompt,
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
                    failed += 1
                    logger.error(
                        "Video failed for media_id=%s: %s — continuing batch.",
                        tile.flow_media_id, exc,
                    )
                    _maybe_inspect_on_error(
                        page, f"vid_{tile.flow_media_id}_recorded", logger,
                    )
                    _record_item(tile, "failed", {
                        "code":    "RECORDED_FLOW_FAILED",
                        "message": str(exc),
                    })
                    emit(
                        "tile_failed",
                        f"Video failed for media_id {tile.flow_media_id}",
                        current=n, total=len(to_submit),
                        details={
                            "media_id": tile.flow_media_id,
                            "tile_id":  tile.flow_tile_id,
                            "edit_id":  tile.edit_id,
                            "error":    {"code": "RECORDED_FLOW_FAILED",
                                         "message": str(exc)},
                        },
                    )
                    page.wait_for_timeout(settings.video_between_products_ms)
                    continue
                except FlowAutomationError as exc:
                    failed += 1
                    logger.error(
                        "Flow error for media_id=%s: %s — continuing batch.",
                        tile.flow_media_id, exc,
                    )
                    _maybe_inspect_on_error(
                        page, f"vid_{tile.flow_media_id}_flow", logger,
                    )
                    _record_item(tile, "failed", {
                        "code":    "FLOW_AUTOMATION_ERROR",
                        "message": str(exc),
                    })
                    emit(
                        "tile_failed",
                        f"Video failed for media_id {tile.flow_media_id}",
                        current=n, total=len(to_submit),
                        details={
                            "media_id": tile.flow_media_id,
                            "tile_id":  tile.flow_tile_id,
                            "edit_id":  tile.edit_id,
                            "error":    {"code": "FLOW_AUTOMATION_ERROR",
                                         "message": str(exc)},
                        },
                    )
                    page.wait_for_timeout(settings.video_between_products_ms)
                    continue
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.exception(
                        "Unexpected error animating media_id=%s "
                        "— continuing batch.",
                        tile.flow_media_id,
                    )
                    _maybe_inspect_on_error(
                        page, f"vid_{tile.flow_media_id}_unexpected", logger,
                    )
                    _record_item(tile, "failed", {
                        "code":    "UNEXPECTED_ERROR",
                        "message": f"{type(exc).__name__}: {exc}",
                    })
                    emit(
                        "tile_failed",
                        f"Video failed for media_id {tile.flow_media_id}",
                        current=n, total=len(to_submit),
                        details={
                            "media_id": tile.flow_media_id,
                            "tile_id":  tile.flow_tile_id,
                            "edit_id":  tile.edit_id,
                            "error":    {"code": "UNEXPECTED_ERROR",
                                         "message": f"{type(exc).__name__}: {exc}"},
                        },
                    )
                    page.wait_for_timeout(settings.video_between_products_ms)
                    continue

                # Success path.
                submitted += 1
                try:
                    mark_submitted(tile.flow_media_id)
                except Exception as exc:  # noqa: BLE001
                    # Persisting the dedup state failed — log + carry
                    # on. The next run will re-submit this tile, but
                    # we'd rather risk a duplicate than crash a batch
                    # mid-way.
                    logger.warning(
                        "mark_submitted(%s) failed: %s",
                        tile.flow_media_id, exc,
                    )
                _record_item(tile, "submitted")
                logger.info(
                    "Video submitted for media_id=%s",
                    tile.flow_media_id,
                )
                emit(
                    "tile_submitted",
                    f"Video submitted for media_id {tile.flow_media_id}",
                    current=n, total=len(to_submit),
                    details={
                        "media_id": tile.flow_media_id,
                        "tile_id":  tile.flow_tile_id,
                        "edit_id":  tile.edit_id,
                    },
                )
                page.wait_for_timeout(settings.video_between_products_ms)

    except FlowAutomationError as exc:
        msg = str(exc)
        if (
            "Could not connect to Chrome" in msg
            or "connect_over_cdp" in msg
            or "9222" in msg
            or "9333" in msg
        ):
            return _failure(job, "CHROME_NOT_REACHABLE", msg)
        return _failure(job, "FLOW_NOT_REACHABLE", msg)
    except Exception as exc:  # noqa: BLE001
        return _failure(
            job,
            "GENERATE_FLOW_VIDEOS_FROM_FAVORITES_FAILED",
            f"{type(exc).__name__}: {exc}",
        )

    elapsed_seconds = round(time.monotonic() - started_at, 2)
    emit(
        "complete",
        "Video generation complete.",
        details={
            "submitted":                  submitted,
            "failed":                     failed,
            "skipped_already_submitted":  skipped,
            "elapsed_seconds":            elapsed_seconds,
        },
    )

    return _success(job, {
        "favorited_images_found":     favorited_images_found,
        "processed":                  submitted + failed,
        "submitted":                  submitted,
        "skipped_already_submitted":  skipped,
        "failed":                     failed,
        "blanket_video_prompt_used":  blanket_prompt,
        "blanket_prompt_source":      blanket_source,
        "elapsed_seconds":            elapsed_seconds,
        "items":                      items,
    })


# ---------------------------------------------------------------------
# Reference-image download helpers (URL → local file)
# ---------------------------------------------------------------------
#
# The SaaS dispatches generate_flow_images jobs with `reference_image_url`
# pointing at a publicly-fetchable copy of each product image
# (https://app.autobof.xyz/uploads/...). The runner downloads each URL
# once, caches it under data/agent_cache/reference_images/<job_id>/
# <item_id>.<ext>, and hands the cached path to perform_recorded_flow().
_IMAGE_CONTENT_TYPES: Dict[str, str] = {
    "image/jpeg":     "jpg",
    "image/jpg":      "jpg",
    "image/pjpeg":    "jpg",
    "image/png":      "png",
    "image/webp":     "webp",
    "image/gif":      "gif",
    "image/bmp":      "bmp",
    "image/tiff":     "tiff",
}

_IMAGE_MAGIC_BYTES = (
    (b"\xff\xd8\xff",                  "jpg"),
    (b"\x89PNG\r\n\x1a\n",             "png"),
    (b"GIF87a",                        "gif"),
    (b"GIF89a",                        "gif"),
    (b"RIFF",                          "webp"),
    (b"BM",                            "bmp"),
    (b"II*\x00",                       "tiff"),
    (b"MM\x00*",                       "tiff"),
)


def _infer_reference_image_ext(
    *,
    content_type: str | None,
    url: str,
    body_head: bytes,
) -> str:
    """Best-effort image extension. Falls back to "jpg"."""
    if content_type:
        ct = content_type.split(";", 1)[0].strip().lower()
        if ct in _IMAGE_CONTENT_TYPES:
            return _IMAGE_CONTENT_TYPES[ct]
    parsed = urlparse(url)
    path_suffix = Path(parsed.path).suffix.lower().lstrip(".")
    if path_suffix:
        if path_suffix == "jpeg":
            return "jpg"
        if path_suffix in {"jpg", "png", "webp", "gif", "bmp", "tiff"}:
            return path_suffix
    for prefix, ext in _IMAGE_MAGIC_BYTES:
        if body_head.startswith(prefix):
            if ext == "webp" and b"WEBP" not in body_head[:16]:
                continue
            return ext
    guess, _ = mimetypes.guess_type(parsed.path)
    if guess and guess in _IMAGE_CONTENT_TYPES:
        return _IMAGE_CONTENT_TYPES[guess]
    return "jpg"


def _sanitize_item_id_for_filename(item_id: str) -> str:
    """Strip filename-unsafe chars from a SaaS Product.id (cuid)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", item_id)[:120] or "item"


def _maybe_inspect_on_error(
    page,
    label: str,
    logger: logging.Logger,
) -> None:
    """Capture a Flow UI DOM dump when an item fails, *if* the
    operator has opted in via `FLOW_INSPECT_ON_ERROR=true`.

    Disabled by default so a hammered runner doesn't spray
    inspection files on every transient hiccup. Late-imports the
    inspector so paths that never error don't pay the JS-blob
    cost. Never raises — diagnostics must not take down a runner.
    """
    try:
        from .flow_inspector import (
            is_inspect_on_error_enabled,
            inspect_and_save_page,
        )
        if not is_inspect_on_error_enabled():
            return
        inspect_and_save_page(page, label=label)
    except Exception as exc:  # noqa: BLE001
        # The inspector itself swallows errors into its 'errors'
        # field; if the import or wrapper failed, log and move on.
        logger.warning("auto-inspect on error failed: %s", exc)


def _redact_url_for_log(url: str) -> str:
    """Drop the query string from a URL before logging.

    Reference image URLs may carry signed-CDN params (Cloudflare R2,
    S3 presigned, etc.). Those grant download access for hours/days,
    so they must never reach our log files or remote log aggregators.
    Preserve scheme/host/path so the log is still actionable; replace
    the query with a sentinel so the reader knows one existed.
    """
    if not url:
        return "<empty-url>"
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return "<invalid-url>"
        redacted_query = "?<redacted>" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{redacted_query}"
    except Exception:  # noqa: BLE001
        return "<invalid-url>"


def _download_reference_image(
    *,
    url: str,
    cache_dir: Path,
    item_id: str,
    logger: logging.Logger,
    timeout_seconds: float = 30.0,
    role: str = "primary",
) -> Path:
    """Download `url` into `cache_dir` and return the cached file path.

    The cached filename embeds the role so a Phase-3 multi-ref item
    can have three distinct cache files (primary / ref2 / ref3)
    without collision. Old single-image callers pass role="primary"
    (the default) and behave exactly as before.

    Uses httpx when present (already a dep via the AI providers);
    falls back to urllib for stripped installs. Raises any exception
    verbatim — the caller maps it to REFERENCE_IMAGE_DOWNLOAD_FAILED.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _sanitize_item_id_for_filename(item_id)
    safe_role = re.sub(r"[^A-Za-z0-9]", "_", role)[:16] or "primary"

    body: bytes
    content_type: str | None = None

    try:
        import httpx  # noqa: WPS433
    except ImportError:
        httpx = None  # type: ignore[assignment]

    if httpx is not None:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            body = resp.content
            content_type = resp.headers.get("content-type")
    else:
        from urllib.request import Request, urlopen  # noqa: WPS433
        req = Request(url, headers={"User-Agent": "flow-bof-runner/0.1"})
        with urlopen(req, timeout=timeout_seconds) as r:  # noqa: S310
            body = r.read()
            content_type = r.headers.get("Content-Type")

    if not body:
        raise ValueError("empty response body")

    ext = _infer_reference_image_ext(
        content_type=content_type, url=url, body_head=body[:32],
    )
    dest = cache_dir / f"{safe_id}_{safe_role}.{ext}"
    dest.write_bytes(body)
    logger.info(
        "cached reference image (%s): %s -> %s (%d bytes, content-type=%s)",
        safe_role, _redact_url_for_log(url), dest, len(body), content_type or "?",
    )
    return dest


def _handle_generate_flow_images(
    job: dict,
    logger: logging.Logger,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Submit image generations in Flow from in-payload items.

    Each item carries an image_prompt plus *one* reference source:

      - reference_image_path : local filesystem path on the runner
                               machine. Used by local dev / debug
                               overrides.
      - reference_image_url  : HTTP(S) URL the runner downloads into
                               data/agent_cache/reference_images/
                               <job_id>/<item_id>.<ext> before calling
                               perform_recorded_flow(). The SaaS uses
                               this for Kalodata-imported references.

    If both are set, the path wins when it exists on disk; otherwise
    the URL is tried. If neither yields a usable file the item fails
    with REFERENCE_IMAGE_MISSING. URL download failures map to
    REFERENCE_IMAGE_DOWNLOAD_FAILED.

    Each result.items[] row carries:
      - reference_source: "path" | "url" | None
      - downloaded_reference_path: set only when reference_source=="url"

    Behavior:
      1. Validate each item up front; surface invalid items in
         result.items as failed with code=INVALID_ITEM /
         REFERENCE_IMAGE_MISSING / REFERENCE_IMAGE_DOWNLOAD_FAILED.
         This means broken inputs are caught BEFORE we open Chrome.
      2. Open the existing Flow tab (never opens a new one — fails
         with FLOW_PAGE_NOT_FOUND if no Flow tab exists).
      3. For each valid item call perform_recorded_flow() with the
         supplied prompt + (path or downloaded-cache) reference.
      4. Continue past per-item failures.

    NON-mutations:
      * Does NOT read or write products.csv.
      * Does NOT touch the manifest.
      * Does NOT modify data/batches/.
      * Does NOT close the user's Chrome.

    wait_mode:
      * "submit_only" (default) — capture_tile=False. Faster; we don't
        wait for the new tile DOM to appear after Generate. The media_id
        in result.items will be null.
      * "capture" — capture_tile=True. perform_recorded_flow waits for
        a new tile and we return its media_id.

    automation_mode override is applied via a temporary env var so the
    underlying load_settings() picks up the right per-mode timing
    defaults. The original env value is restored after settings load.
    """
    payload = job.get("payload") or {}
    emit = _make_emitter(job, progress_callback)
    started_at = time.monotonic()

    # ---- Coerce payload --------------------------------------------------
    items_raw = payload.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        return _failure(
            job,
            "MISSING_ITEMS",
            "payload.items must be a non-empty list of "
            "{item_id, product_name, image_prompt, reference_image_path | "
            "reference_image_url} objects.",
        )

    try:
        limit = int(payload.get("limit") or len(items_raw))
    except (TypeError, ValueError):
        limit = len(items_raw)
    if limit <= 0:
        limit = len(items_raw)

    wait_mode = (payload.get("wait_mode") or "submit_only").strip().lower()
    if wait_mode not in {"capture", "submit_only"}:
        wait_mode = "submit_only"

    automation_mode_override = (payload.get("automation_mode") or "").strip().lower()

    # ---- Phase-A imports (no Playwright dependency) --------------------
    # Pulling these in before the heavyweight Playwright modules lets a
    # batch with zero valid items return successfully even on a host
    # that doesn't have Playwright installed — useful for SaaS-side
    # "preview validation" jobs that just want to check whether the
    # caller's items would have validated.
    try:
        from .config import load_settings
    except ImportError as exc:
        return _failure(
            job,
            "AGENT_DEPENDENCY_MISSING",
            f"Cannot import .config: {exc}",
        )

    # ---- Settings, with optional per-job automation_mode override ------
    saved_env = os.environ.get("AUTOMATION_MODE")
    try:
        if automation_mode_override:
            os.environ["AUTOMATION_MODE"] = automation_mode_override
        try:
            settings = load_settings()
        except Exception as exc:  # noqa: BLE001
            return _failure(
                job,
                "BAD_AGENT_CONFIG",
                f"load_settings raised: {type(exc).__name__}: {exc}",
            )
    finally:
        if automation_mode_override:
            if saved_env is None:
                os.environ.pop("AUTOMATION_MODE", None)
            else:
                os.environ["AUTOMATION_MODE"] = saved_env

    # ---- Pre-flight item validation -------------------------------------
    # Done BEFORE opening Chrome so we don't waste a browser session on a
    # batch full of typos. Invalid items become "failed" entries in the
    # result and don't block the valid ones.
    #
    # Resolution order per item:
    #   1. reference_image_path (if provided AND the file exists on disk)
    #         → reference_source="path"
    #   2. reference_image_url  (if provided)
    #         → download to data/agent_cache/reference_images/<job_id>/
    #         → reference_source="url"
    #   3. Otherwise: REFERENCE_IMAGE_MISSING.
    job_id_for_cache = (
        (str(job.get("job_id") or "")).strip() or f"adhoc-{int(time.monotonic())}"
    )
    cache_dir = (
        settings.repo_root / "data" / "agent_cache" / "reference_images"
        / _sanitize_item_id_for_filename(job_id_for_cache)
    )

    valid_items: list[dict] = []
    items_out: list[dict] = []
    pre_failed = 0
    for idx, raw_item in enumerate(items_raw):
        if not isinstance(raw_item, dict):
            pre_failed += 1
            items_out.append({
                "item_id":      f"item_{idx + 1:02d}",
                "product_name": "",
                "status":       "failed",
                "media_id":     None,
                "reference_source": None,
                "error": {
                    "code":    "INVALID_ITEM",
                    "message": f"items[{idx}] is not an object",
                },
            })
            continue

        item_id = (str(raw_item.get("item_id") or "")).strip() or f"item_{idx + 1:02d}"
        product_name = (str(raw_item.get("product_name") or "")).strip()
        ref_path_raw = (str(raw_item.get("reference_image_path") or "")).strip()
        ref_url_raw = (str(raw_item.get("reference_image_url") or "")).strip()
        prompt = str(raw_item.get("image_prompt") or "")

        # Phase 3 — multi-reference image list. When present and
        # non-empty, this is the authoritative source: the legacy
        # singular fields above are mirrors of the primary entry,
        # kept for back-compat with older runners (i.e. us, if a
        # workspace pins to a runner that predates this code path).
        # Format: [{role: "primary"|"ref2"|"ref3", url: str?, path: str?}, ...]
        reference_images_raw = raw_item.get("reference_images") or []
        if not isinstance(reference_images_raw, list):
            reference_images_raw = []

        if not prompt.strip():
            pre_failed += 1
            items_out.append({
                "item_id":      item_id,
                "product_name": product_name,
                "status":       "failed",
                "media_id":     None,
                "reference_source": None,
                "error": {
                    "code":    "INVALID_ITEM",
                    "message": "image_prompt is required",
                },
            })
            continue

        # ---- Resolve reference images to local paths -----------------
        #
        # Two paths:
        #   A. Multi-ref (Phase 3): reference_images array is non-empty.
        #      Resolve each entry to a local path; preserve order.
        #   B. Legacy single-ref: only reference_image_path /
        #      reference_image_url were sent. One image, role="primary".
        #
        # Both paths produce the same shape: a list of (role, local_path)
        # tuples in attach order, plus a `reference_source` summary
        # string for the report.
        resolved_refs: list[tuple[str, Path]] = []
        ref_source_summary: str | None = None
        download_error: str | None = None

        def _resolve_one(
            *, role: str, path_raw: str, url_raw: str,
        ) -> tuple[Path, str] | None:
            """Resolve one reference (path → URL fallback). Returns
            (local_path, source) or None when neither path nor URL was
            provided. Raises on download failure.
            """
            if path_raw:
                candidate = Path(path_raw)
                if not candidate.is_absolute():
                    candidate = settings.repo_root / candidate
                if candidate.exists() and candidate.is_file():
                    return candidate, "path"
            if url_raw:
                downloaded = _download_reference_image(
                    url=url_raw,
                    cache_dir=cache_dir,
                    item_id=item_id,
                    logger=logger,
                    role=role,
                )
                return downloaded, "url"
            return None

        if reference_images_raw:
            # Path A — multi-ref. Iterate the array, resolve each.
            for ref_entry in reference_images_raw:
                if not isinstance(ref_entry, dict):
                    continue
                role = (str(ref_entry.get("role") or "")).strip() or "primary"
                entry_url = (str(ref_entry.get("url") or "")).strip()
                entry_path = (str(ref_entry.get("path") or "")).strip()
                try:
                    resolved = _resolve_one(
                        role=role, path_raw=entry_path, url_raw=entry_url,
                    )
                except Exception as exc:  # noqa: BLE001
                    download_error = (
                        f"[{role}] {type(exc).__name__}: {exc} "
                        f"(url={_redact_url_for_log(entry_url)})"
                    )
                    break
                if resolved is not None:
                    resolved_refs.append((role, resolved[0]))
                    # The summary string reports the source of the
                    # primary; supplementary refs are assumed to share
                    # the same transport.
                    if role == "primary" and ref_source_summary is None:
                        ref_source_summary = resolved[1]
        else:
            # Path B — legacy single-ref. Same resolver, called once.
            try:
                resolved = _resolve_one(
                    role="primary",
                    path_raw=ref_path_raw,
                    url_raw=ref_url_raw,
                )
            except Exception as exc:  # noqa: BLE001
                download_error = (
                    f"{type(exc).__name__}: {exc} "
                    f"(url={_redact_url_for_log(ref_url_raw)})"
                )
                resolved = None
            if resolved is not None:
                resolved_refs.append(("primary", resolved[0]))
                ref_source_summary = resolved[1]

        # Failures at the resolution stage. Two distinct error codes
        # so the SaaS can tell a download failure (transient — try
        # again) from a missing-config error (user needs to attach
        # an image).
        if download_error:
            pre_failed += 1
            items_out.append({
                "item_id":      item_id,
                "product_name": product_name,
                "status":       "failed",
                "media_id":     None,
                "reference_source": "url",
                "error": {
                    "code":    "REFERENCE_IMAGE_DOWNLOAD_FAILED",
                    "message": download_error,
                },
            })
            continue
        if not resolved_refs:
            pre_failed += 1
            items_out.append({
                "item_id":      item_id,
                "product_name": product_name,
                "status":       "failed",
                "media_id":     None,
                "reference_source": None,
                "error": {
                    "code":    "REFERENCE_IMAGE_MISSING",
                    "message": (
                        "Provide either reference_image_path (local "
                        "file), reference_image_url, or a non-empty "
                        "reference_images array."
                    ),
                },
            })
            continue

        # Primary = first resolved ref. Phase-3 multi-ref sends them
        # in role order so this is correct; legacy single-ref always
        # produces just one entry here.
        primary_path = resolved_refs[0][1]
        additional_paths = [str(p) for (_, p) in resolved_refs[1:]]

        valid_items.append({
            "item_id":                  item_id,
            "product_name":             product_name,
            "reference_image_path":     str(primary_path),
            "reference_image_paths":    [str(p) for (_, p) in resolved_refs],
            "additional_image_paths":   additional_paths,
            "reference_source":         ref_source_summary,
            "downloaded_reference_path": str(primary_path)
                if ref_source_summary == "url" else None,
            "image_prompt":             prompt,
        })

    # Apply limit on the validated set so invalid items don't consume
    # slots from valid ones.
    valid_items = valid_items[:limit]

    emit(
        "preparing",
        "Preparing image generation batch...",
        details={
            "items_received":  len(items_raw),
            "valid":           len(valid_items),
            "invalid":         pre_failed,
            "wait_mode":       wait_mode,
            "automation_mode": settings.automation_mode,
            "limit":           limit,
        },
    )

    submitted = 0
    failed = pre_failed  # invalid items count as failed for the summary

    if not valid_items:
        # Nothing to submit. Return a well-formed envelope with the
        # invalid-item failures we recorded above.
        elapsed = round(time.monotonic() - started_at, 2)
        emit("complete", "Image generation complete.", details={
            "submitted":       submitted,
            "failed":          failed,
            "elapsed_seconds": elapsed,
        })
        return _success(job, {
            "items_received":  len(items_raw),
            "processed":       submitted + failed,
            "submitted":       submitted,
            "failed":          failed,
            "items":           items_out,
            "elapsed_seconds": elapsed,
        })

    # ---- Phase-B imports (Playwright-dependent) ------------------------
    # Only reached when there's actual browser work to do. A pure
    # validation pass with zero valid items never imports these and
    # so works on bare hosts without Playwright.
    try:
        from .flow_automation import (
            FlowAutomationError,
            open_flow_browser,
        )
        from .recorded_flow import (
            RecordedFlowError,
            perform_recorded_flow,
        )
    except ImportError as exc:
        return _failure(
            job,
            "AGENT_DEPENDENCY_MISSING",
            f"Cannot import image-flow modules: {exc}",
        )

    # ---- Open browser, find tab, iterate -------------------------------
    try:
        with open_flow_browser(settings, logger) as session:
            page = _find_existing_flow_page(session, settings)
            if page is None:
                return _failure(
                    job,
                    "FLOW_PAGE_NOT_FOUND",
                    (
                        f"No tab matching {settings.flow_labs_url} is open. "
                        "Open Flow Labs in the debug Chrome window and try "
                        "again."
                    ),
                )

            logger.info(
                "Generating %d image(s) (mode=%s, wait_mode=%s).",
                len(valid_items), settings.automation_mode, wait_mode,
            )

            # Risk-engine detection state. Set on first hit; the loop
            # exits at the next iteration boundary so the in-flight
            # item finishes (or fails) cleanly without partial state.
            flow_risk_code: str | None = None
            flow_risk_after_item: int = 0

            for n, item in enumerate(valid_items, start=1):
                # Google Flow's anti-abuse risk engine occasionally
                # flags our session and starts rejecting submits with
                # "We noticed some unusual activity" / "Too many
                # requests" / "Try again later" tiles. Detecting
                # between items (before burning another submit into
                # an already-flagged tab) lets the batch stop early
                # and surface a specific error code to the SaaS, who
                # can then show a cooldown banner to the user.
                #
                # Detection is cheap (one page.evaluate over body
                # innerText, no waits). Skipped on the first
                # iteration because there's no prior submit yet —
                # the very first item runs unconditionally.
                if n > 1:
                    from .recorded_flow import detect_flow_unusual_activity
                    risk = detect_flow_unusual_activity(page)
                    if risk:
                        logger.warning(
                            "Flow risk-engine triggered after item %d (%s). "
                            "Stopping batch — the SaaS will surface a "
                            "cooldown banner. Further submits would just "
                            "push the risk score higher.",
                            n - 1, risk,
                        )
                        flow_risk_code = risk
                        flow_risk_after_item = n - 1
                        break

                logger.info(
                    "--- [%d/%d] item_id=%s product=%s",
                    n, len(valid_items),
                    item["item_id"],
                    item["product_name"] or "-",
                )
                emit(
                    "processing_item",
                    f"Submitting image {n} of {len(valid_items)}...",
                    current=n,
                    total=len(valid_items),
                    details={
                        "item_id":      item["item_id"],
                        "product_name": item["product_name"],
                    },
                )

                # Centralised per-item Flow-UI cleanup:
                #   - dismiss stale menus / dialogs / agent pills
                #   - re-apply 9:16 / 1x / Nano Banana Pro settings
                # Never raises; the report goes into the progress
                # event for diagnostics. Toggle via FLOW_UI_PREP_*
                # env vars when something looks off; see
                # src/flow_ui_prep.py.
                try:
                    from .flow_ui_prep import prepare_flow_for_image_generation
                    prep_report = prepare_flow_for_image_generation(
                        page,
                        logger=logger,
                        selector_timeout_ms=settings.selector_timeout_ms,
                    )
                    if prep_report and not prep_report.get("skipped"):
                        emit(
                            "flow_ui_prep",
                            "Flow UI prep complete",
                            current=n,
                            total=len(valid_items),
                            details={"prep": prep_report},
                        )
                except Exception as exc:  # noqa: BLE001
                    # Prep failure must NEVER kill the job — log and
                    # let perform_recorded_flow attempt the submit.
                    logger.warning(
                        "flow-ui-prep raised before image submit (item=%s): %s",
                        item["item_id"], exc,
                    )

                try:
                    tiles = perform_recorded_flow(
                        page,
                        item["reference_image_path"],
                        item["image_prompt"],
                        additional_image_paths=item.get("additional_image_paths") or None,
                        logger=logger,
                        selector_timeout_ms=settings.selector_timeout_ms,
                        generation_timeout_seconds=settings.generation_timeout_seconds,
                        verify_generation_started=settings.verify_generation_started,
                        wait_for_result=False,
                        capture_tile=(wait_mode == "capture"),
                        capture_timeout_seconds=settings.capture_timeout_seconds,
                        capture_sibling_window_ms=settings.image_sibling_window_ms,
                        fast_submit_mode=settings.image_fast_submit_mode,
                        debug_screenshots=settings.debug_screenshots,
                    )
                except RecordedFlowError as exc:
                    failed += 1
                    logger.error(
                        "Image failed for item_id=%s: %s — continuing batch.",
                        item["item_id"], exc,
                    )
                    _maybe_inspect_on_error(
                        page, f"img_{item['item_id']}_recorded", logger,
                    )
                    items_out.append({
                        "item_id":      item["item_id"],
                        "product_name": item["product_name"],
                        "status":       "failed",
                        "media_id":     None,
                        "error": {
                            "code":    "RECORDED_FLOW_FAILED",
                            "message": str(exc),
                        },
                    })
                    emit(
                        "item_failed",
                        f"Image failed for item_id {item['item_id']}",
                        current=n, total=len(valid_items),
                        details={
                            "item_id":      item["item_id"],
                            "product_name": item["product_name"],
                            "error": {
                                "code":    "RECORDED_FLOW_FAILED",
                                "message": str(exc),
                            },
                        },
                    )
                    page.wait_for_timeout(settings.image_between_products_ms + random.randint(0, 2000))
                    continue
                except FlowAutomationError as exc:
                    failed += 1
                    logger.error(
                        "Flow error for item_id=%s: %s — continuing batch.",
                        item["item_id"], exc,
                    )
                    _maybe_inspect_on_error(
                        page, f"img_{item['item_id']}_flow", logger,
                    )
                    items_out.append({
                        "item_id":      item["item_id"],
                        "product_name": item["product_name"],
                        "status":       "failed",
                        "media_id":     None,
                        "error": {
                            "code":    "FLOW_AUTOMATION_ERROR",
                            "message": str(exc),
                        },
                    })
                    emit(
                        "item_failed",
                        f"Image failed for item_id {item['item_id']}",
                        current=n, total=len(valid_items),
                        details={
                            "item_id":      item["item_id"],
                            "product_name": item["product_name"],
                            "error": {
                                "code":    "FLOW_AUTOMATION_ERROR",
                                "message": str(exc),
                            },
                        },
                    )
                    page.wait_for_timeout(settings.image_between_products_ms + random.randint(0, 2000))
                    continue
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.exception(
                        "Unexpected error for item_id=%s — continuing batch.",
                        item["item_id"],
                    )
                    _maybe_inspect_on_error(
                        page, f"img_{item['item_id']}_unexpected", logger,
                    )
                    items_out.append({
                        "item_id":      item["item_id"],
                        "product_name": item["product_name"],
                        "status":       "failed",
                        "media_id":     None,
                        "error": {
                            "code":    "UNEXPECTED_ERROR",
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    })
                    emit(
                        "item_failed",
                        f"Image failed for item_id {item['item_id']}",
                        current=n, total=len(valid_items),
                        details={
                            "item_id":      item["item_id"],
                            "product_name": item["product_name"],
                            "error": {
                                "code":    "UNEXPECTED_ERROR",
                                "message": f"{type(exc).__name__}: {exc}",
                            },
                        },
                    )
                    page.wait_for_timeout(settings.image_between_products_ms + random.randint(0, 2000))
                    continue

                # Success path. tiles is the list of newly captured
                # TileInfos when wait_mode=capture; empty otherwise.
                # First tile is the most likely match (Flow renders the
                # new generation as a fresh sibling).
                submitted += 1
                captured_media_id = (
                    tiles[0].flow_media_id if tiles else None
                )
                items_out.append({
                    "item_id":      item["item_id"],
                    "product_name": item["product_name"],
                    "status":       "submitted",
                    "media_id":     captured_media_id,
                    "error":        None,
                })
                emit(
                    "item_submitted",
                    f"Image submitted for item_id {item['item_id']}",
                    current=n, total=len(valid_items),
                    details={
                        "item_id":      item["item_id"],
                        "product_name": item["product_name"],
                        "media_id":     captured_media_id,
                    },
                )
                page.wait_for_timeout(settings.image_between_products_ms + random.randint(0, 2000))

    except FlowAutomationError as exc:
        msg = str(exc)
        if (
            "Could not connect to Chrome" in msg
            or "connect_over_cdp" in msg
            or "9222" in msg
            or "9333" in msg
        ):
            return _failure(job, "CHROME_NOT_REACHABLE", msg)
        return _failure(job, "FLOW_NOT_REACHABLE", msg)
    except Exception as exc:  # noqa: BLE001
        return _failure(
            job,
            "GENERATE_FLOW_IMAGES_FAILED",
            f"{type(exc).__name__}: {exc}",
        )

    elapsed = round(time.monotonic() - started_at, 2)

    # Risk-engine short-circuit. The loop sets `flow_risk_code` when
    # Flow's anti-abuse text appears between submits; we bail with a
    # specific failure code so the SaaS can surface a cooldown banner
    # to the user instead of treating the batch as a normal partial
    # success.
    if flow_risk_code:
        emit(
            "rate_limited",
            f"Google Flow risk-engine error detected ({flow_risk_code}). "
            f"Stopped after {submitted} successful submit(s); "
            f"{len(valid_items) - flow_risk_after_item} item(s) "
            f"unsubmitted. Wait 30-60 minutes before trying again.",
            details={
                "code":                "FLOW_RATE_LIMIT_OR_SUSPICIOUS_ACTIVITY",
                "risk_phrase":         flow_risk_code,
                "stopped_after_item":  flow_risk_after_item,
                "submitted":           submitted,
                "failed":              failed,
                "unsubmitted":         len(valid_items) - flow_risk_after_item,
                "elapsed_seconds":     elapsed,
            },
        )
        return _failure(
            job,
            "FLOW_RATE_LIMIT_OR_SUSPICIOUS_ACTIVITY",
            (
                f"Google Flow flagged the session as 'unusual activity' "
                f"after item {flow_risk_after_item}. Stopped to avoid "
                f"raising the risk score further. Wait 30-60 minutes "
                f"and try again; consider running smaller batches with "
                f"longer between-product delays."
            ),
            details={
                "risk_phrase":         flow_risk_code,
                "stopped_after_item":  flow_risk_after_item,
                "submitted":           submitted,
                "failed":              failed,
                "unsubmitted":         len(valid_items) - flow_risk_after_item,
                "items":               items_out,
                "elapsed_seconds":     elapsed,
            },
        )

    emit("complete", "Image generation complete.", details={
        "submitted":       submitted,
        "failed":          failed,
        "elapsed_seconds": elapsed,
    })

    return _success(job, {
        "items_received":  len(items_raw),
        "processed":       submitted + failed,
        "submitted":       submitted,
        "failed":          failed,
        "items":           items_out,
        "elapsed_seconds": elapsed,
    })


_JOB_HANDLERS: Dict[
    str,
    Callable[[dict, logging.Logger, Optional[ProgressCallback]], dict],
] = {
    "health_check":                          _handle_health_check,
    "scan_favorited_images":                 _handle_scan_favorited_images,
    "check_flow_connection":                 _handle_check_flow_connection,
    "generate_flow_videos_from_favorites":   _handle_generate_flow_videos_from_favorites,
    "generate_flow_images":                  _handle_generate_flow_images,
}


def known_job_types() -> list[str]:
    return sorted(_JOB_HANDLERS)


def handle_agent_job(
    job: dict,
    logger: logging.Logger | None = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Dispatch a job envelope to the right handler. Never raises.

    The caller passes a dict that conforms (loosely) to the input
    envelope shape above. Missing fields produce a structured failed
    response instead of an exception, so this is safe to call from
    untrusted JSON.

    ``progress_callback`` is an optional one-argument function that
    receives a fully-formed progress event dict at each stage of a
    long-running job. Handlers that don't need to emit progress simply
    accept and ignore it. Short jobs (health_check etc.) emit nothing.
    Long jobs (generate_flow_videos_from_favorites) emit events per
    the schema in ``docs/JOB_PROTOCOL.md``.

    Unknown job_type values return a ``UNKNOWN_JOB_TYPE`` failure
    rather than raising, again so the caller never has to wrap this in
    a try.
    """
    if logger is None:
        logger = logging.getLogger("agent")

    if not isinstance(job, dict):
        return _failure(
            {}, "BAD_ENVELOPE",
            f"job must be a dict, got {type(job).__name__}",
        )

    job_type = (job.get("job_type") or "").strip()
    if not job_type:
        return _failure(job, "MISSING_JOB_TYPE", "job_type is required")

    handler = _JOB_HANDLERS.get(job_type)
    if handler is None:
        return _failure(
            job,
            "UNKNOWN_JOB_TYPE",
            f"No handler for job_type={job_type!r}. "
            f"Known types: {known_job_types()}",
        )

    try:
        return handler(job, logger, progress_callback)
    except Exception as exc:  # noqa: BLE001
        # Last-resort safety net. The handler itself is supposed to
        # surface its own failures as structured responses, but if it
        # raises (logic bug, OS-level surprise), we still return a
        # well-formed envelope rather than letting the exception
        # escape into a caller that doesn't expect one.
        logger.error(
            "agent job %s crashed: %s", job_type, exc, exc_info=True,
        )
        return _failure(
            job,
            f"{job_type.upper()}_FAILED",
            f"{type(exc).__name__}: {exc}",
            {"traceback": traceback.format_exc().splitlines()[-12:]},
        )
