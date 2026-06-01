"""Central configuration. Selectors live here, not in flow_automation.py."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


BROWSER_MODE_REMOTE_DEBUGGING = "remote_debugging"
BROWSER_MODE_PERSISTENT_PROFILE = "persistent_profile"
VALID_BROWSER_MODES = {BROWSER_MODE_REMOTE_DEBUGGING, BROWSER_MODE_PERSISTENT_PROFILE}


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    inputs_dir: Path
    products_json: Path
    products_csv: Path
    incoming_images_dir: Path
    reference_images_dir: Path
    manifest_path: Path
    outputs_dir: Path
    images_dir: Path
    logs_dir: Path
    browser_mode: str
    chrome_cdp_url: str
    browser_user_data_dir: Path
    flow_labs_url: str
    headless: bool
    generation_timeout_seconds: int
    selector_timeout_ms: int
    slow_mo_ms: int
    save_output_image: bool
    verify_generation_started: bool
    capture_timeout_seconds: int
    video_tile_settle_ms: int
    video_after_hover_ms: int
    video_after_menu_click_ms: int
    video_between_products_ms: int
    video_retry_count: int
    image_between_products_ms: int
    image_ui_settle_ms: int
    automation_mode: str
    debug_screenshots: bool
    # Phase 4.1 — image flow speed
    image_fast_submit_mode: bool
    image_sibling_window_ms: int
    # Phase 5.1 — strict blanket video prompt. See docs/UI_GUIDE.md.
    use_blanket_video_prompt: bool
    blanket_video_prompt: str


DEFAULT_BLANKET_VIDEO_PROMPT = (
    "Slow handheld iPhone-style push-in toward the product. A hand "
    "enters the frame and gently taps the product once, as if the "
    "person recording is checking it on the shelf. Preserve the exact "
    "product appearance. Keep the environment stable and realistic. "
    "No morphing, no dramatic camera move, no cinematic lighting."
)


AUTOMATION_MODE_SAFE = "safe"
AUTOMATION_MODE_BALANCED = "balanced"
AUTOMATION_MODE_FAST = "fast"
VALID_AUTOMATION_MODES = {AUTOMATION_MODE_SAFE, AUTOMATION_MODE_BALANCED, AUTOMATION_MODE_FAST}


def _automation_mode_defaults(mode: str) -> dict[str, int]:
    """Per-mode timing defaults. Individual env vars override these."""
    if mode == AUTOMATION_MODE_FAST:
        return dict(
            image_between_products_ms=100,
            image_ui_settle_ms=80,
            video_tile_settle_ms=300,
            video_after_hover_ms=300,
            video_after_menu_click_ms=200,
            video_between_products_ms=400,
            video_retry_count=2,
        )
    if mode == AUTOMATION_MODE_BALANCED:
        return dict(
            image_between_products_ms=200,
            image_ui_settle_ms=150,
            video_tile_settle_ms=400,
            video_after_hover_ms=400,
            video_after_menu_click_ms=300,
            video_between_products_ms=700,
            video_retry_count=3,
        )
    # safe (default)
    return dict(
        image_between_products_ms=300,
        image_ui_settle_ms=250,
        video_tile_settle_ms=500,
        video_after_hover_ms=500,
        video_after_menu_click_ms=400,
        video_between_products_ms=900,
        video_retry_count=3,
    )


def load_settings() -> Settings:
    user_data_dir = Path(os.getenv("BROWSER_USER_DATA_DIR", ".browser_profile"))
    if not user_data_dir.is_absolute():
        user_data_dir = REPO_ROOT / user_data_dir

    automation_mode = (
        os.getenv("AUTOMATION_MODE") or AUTOMATION_MODE_SAFE
    ).strip().lower()
    if automation_mode not in VALID_AUTOMATION_MODES:
        automation_mode = AUTOMATION_MODE_SAFE
    mode_defaults = _automation_mode_defaults(automation_mode)

    browser_mode = (os.getenv("BROWSER_MODE") or BROWSER_MODE_REMOTE_DEBUGGING).strip().lower()
    if browser_mode not in VALID_BROWSER_MODES:
        raise ValueError(
            f"BROWSER_MODE={browser_mode!r} is invalid. "
            f"Use one of: {sorted(VALID_BROWSER_MODES)}"
        )

    return Settings(
        repo_root=REPO_ROOT,
        inputs_dir=REPO_ROOT / "inputs",
        products_json=REPO_ROOT / "inputs" / "products.json",
        products_csv=REPO_ROOT / "inputs" / "products.csv",
        incoming_images_dir=REPO_ROOT / "inputs" / "incoming_images",
        reference_images_dir=REPO_ROOT / "inputs" / "reference_images",
        manifest_path=REPO_ROOT / "inputs" / "prompt_manifest.md",
        outputs_dir=REPO_ROOT / "outputs",
        images_dir=REPO_ROOT / "outputs" / "images",
        logs_dir=REPO_ROOT / "outputs" / "logs",
        browser_mode=browser_mode,
        chrome_cdp_url=os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222"),
        browser_user_data_dir=user_data_dir,
        flow_labs_url=os.getenv("FLOW_LABS_URL", "https://labs.google/flow"),
        headless=_env_bool("HEADLESS", False),
        generation_timeout_seconds=_env_int("GENERATION_TIMEOUT_SECONDS", 180),
        selector_timeout_ms=_env_int("SELECTOR_TIMEOUT_MS", 15000),
        slow_mo_ms=_env_int("SLOW_MO_MS", 0),
        save_output_image=_env_bool("SAVE_OUTPUT_IMAGE", False),
        verify_generation_started=_env_bool("VERIFY_GENERATION_STARTED", False),
        capture_timeout_seconds=_env_int("CAPTURE_TIMEOUT_SECONDS", 60),
        video_tile_settle_ms=_env_int(
            "VIDEO_TILE_SETTLE_MS", mode_defaults["video_tile_settle_ms"]
        ),
        video_after_hover_ms=_env_int(
            "VIDEO_AFTER_HOVER_MS", mode_defaults["video_after_hover_ms"]
        ),
        video_after_menu_click_ms=_env_int(
            "VIDEO_AFTER_MENU_CLICK_MS", mode_defaults["video_after_menu_click_ms"]
        ),
        video_between_products_ms=_env_int(
            "VIDEO_BETWEEN_PRODUCTS_MS", mode_defaults["video_between_products_ms"]
        ),
        video_retry_count=_env_int(
            "VIDEO_RETRY_COUNT", mode_defaults["video_retry_count"]
        ),
        image_between_products_ms=_env_int(
            "IMAGE_BETWEEN_PRODUCTS_MS", mode_defaults["image_between_products_ms"]
        ),
        image_ui_settle_ms=_env_int(
            "IMAGE_UI_SETTLE_MS", mode_defaults["image_ui_settle_ms"]
        ),
        automation_mode=automation_mode,
        debug_screenshots=_env_bool("DEBUG_SCREENSHOTS", False),
        # Fast-submit: don't wait for media_ids per row; sweep once at
        # the end of the batch. Set IMAGE_FAST_SUBMIT_MODE=false to
        # restore the old per-row two-phase wait.
        image_fast_submit_mode=_env_bool("IMAGE_FAST_SUBMIT_MODE", True),
        image_sibling_window_ms=_env_int("IMAGE_SIBLING_WINDOW_MS", 2000),
        # Strict blanket video prompt. When True, video generation
        # ignores per-product video_prompt entirely and uses the
        # universal prompt below. Default True to avoid mismatches when
        # the user manually regenerates/rebinds images.
        use_blanket_video_prompt=_env_bool("USE_BLANKET_VIDEO_PROMPT", True),
        blanket_video_prompt=(
            os.getenv("BLANKET_VIDEO_PROMPT") or DEFAULT_BLANKET_VIDEO_PROMPT
        ).strip(),
    )


# Store + placement mapping rules. Use these to fill defaults when products.json
# leaves a field blank. Keys are normalized lowercase category names.
CATEGORY_RULES: dict[str, dict[str, str]] = {
    "fitness": {
        "store": "Dick's Sporting Goods",
        "section": "fitness equipment section",
        "placement_type": "floor display",
    },
    "sports": {
        "store": "Dick's Sporting Goods",
        "section": "sports equipment section",
        "placement_type": "floor display",
    },
    "electronics": {
        "store": "Best Buy",
        "section": "electronics section",
        "placement_type": "retail shelf display",
    },
    "tech": {
        "store": "Best Buy",
        "section": "electronics section",
        "placement_type": "retail shelf display",
    },
    "home": {
        "store": "Target",
        "section": "home goods section",
        "placement_type": "retail shelf display",
    },
    "kitchen": {
        "store": "Target",
        "section": "kitchen section",
        "placement_type": "retail shelf display",
    },
    "tools": {
        "store": "Home Depot",
        "section": "tools aisle",
        "placement_type": "retail shelf display",
    },
    "appliances": {
        "store": "Home Depot",
        "section": "appliances section",
        "placement_type": "floor display",
    },
    "clothing": {
        "store": "Target",
        "section": "clothing section",
        "placement_type": "folded display table",
    },
    "apparel": {
        "store": "Target",
        "section": "clothing section",
        "placement_type": "folded display table",
    },
    "beauty": {
        "store": "Sephora",
        "section": "beauty section",
        "placement_type": "beauty shelf display",
    },
    "skincare": {
        "store": "Sephora",
        "section": "skincare section",
        "placement_type": "beauty shelf display",
    },
    "makeup": {
        "store": "Ulta",
        "section": "makeup section",
        "placement_type": "beauty shelf display",
    },
    "haircare": {
        "store": "Ulta",
        "section": "haircare section",
        "placement_type": "beauty shelf display",
    },
    "baby": {
        "store": "Target",
        "section": "baby section",
        "placement_type": "retail shelf display",
    },
    "kids": {
        "store": "Target",
        "section": "kids section",
        "placement_type": "retail shelf display",
    },
    "pet": {
        "store": "PetSmart",
        "section": "pet supplies section",
        "placement_type": "retail shelf display",
    },
    "supplements": {
        "store": "Target",
        "section": "health and wellness section",
        "placement_type": "retail shelf display",
    },
    "health": {
        "store": "Target",
        "section": "health and wellness section",
        "placement_type": "retail shelf display",
    },
    "wellness": {
        "store": "Target",
        "section": "health and wellness section",
        "placement_type": "retail shelf display",
    },
    "misc": {
        "store": "Target",
        "section": "relevant department",
        "placement_type": "retail shelf display",
    },
}


# All Flow Labs locators (new-project button, aspect/variant/model
# pickers, plus, prompt input, generate arrow, result images) now live
# in src/recorded_flow.py — the paste target for action-recorder output.
# See docs/record-flow-labs-actions.md.
