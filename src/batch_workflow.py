"""Batch + product-card data layer for the AI Product Intake page.

Each batch lives under `data/batches/<batch_id>/`:

    products.json                  # list[ProductCard] as JSON
    prompt_manifest.md             # exported from products.json
    reference_images/
        <product_id>_primary.<ext>
        <product_id>_ref2.<ext>
        <product_id>_ref3.<ext>

A batch is just a directory; `create_batch` makes one. Products are
plain dicts/dataclasses persisted as JSON — deliberately not SQLite
yet, because the data is small, human-editable, and Git-friendly.

The export step writes the manifest into the batch folder AND syncs it
+ its reference images to the existing CLI locations
(`inputs/prompt_manifest.md`, `inputs/reference_images/`), so the
existing `--load-manifest` / `--generate-images` flow keeps working
unchanged.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .config import REPO_ROOT, Settings


BATCHES_DIR = REPO_ROOT / "data" / "batches"

PRODUCT_STATUS_DRAFT = "draft"
PRODUCT_STATUS_READY = "ready"
PRODUCT_STATUS_EXPORTED = "exported"

REFERENCE_ROLES = ("primary", "ref2", "ref3")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class ProductCard:
    id: str = ""
    product_name: str = ""
    # Unmodified title from the source (TikTok / Kalodata / etc.) — kept
    # separate so cleaning the displayed `product_name` doesn't lose
    # the original wording.
    original_title: str = ""
    tiktok_url: str = ""
    product_description: str = ""
    notes: str = ""
    reference_images: list[str] = field(default_factory=list)  # repo-relative paths
    image_prompt: str = ""
    video_prompt: str = ""
    hook: str = ""
    caption: str = ""
    category: str = ""
    store_environment: str = ""
    placement_type: str = ""
    status: str = PRODUCT_STATUS_DRAFT
    warnings: list[str] = field(default_factory=list)

    def has_prompts(self) -> bool:
        """Legacy: image+video both present.

        Most code should call :meth:`is_ready_to_export` instead — under
        the strict blanket-video-prompt mode (USE_BLANKET_VIDEO_PROMPT=true,
        which is the default) the video_prompt is optional because the
        manifest fills it from the blanket prompt at export time.
        """
        return bool(self.image_prompt.strip() and self.video_prompt.strip())

    def has_image(self) -> bool:
        return bool(self.reference_images)

    def is_ready_to_export(self) -> bool:
        """Ready to export = name + reference image + image_prompt.

        Video prompt is no longer required at this gate. Under the
        default strict mode, ``export_manifest`` fills Video Prompt with
        the universal blanket prompt for every product that doesn't
        have its own.
        """
        return bool(
            self.product_name.strip()
            and self.has_image()
            and self.image_prompt.strip()
        )

    def primary_reference(self) -> Optional[str]:
        for r in self.reference_images:
            if "_primary." in Path(r).name:
                return r
        return self.reference_images[0] if self.reference_images else None

    def taken_roles(self) -> set[str]:
        """Return the reference roles (primary/ref2/ref3) currently filled."""
        roles: set[str] = set()
        for r in self.reference_images:
            name = Path(r).name
            for role in REFERENCE_ROLES:
                if f"_{role}." in name:
                    roles.add(role)
                    break
        return roles

    def next_available_role(self) -> Optional[str]:
        """Return the next empty role, or None if all 3 are filled."""
        taken = self.taken_roles()
        for role in REFERENCE_ROLES:
            if role not in taken:
                return role
        return None


# ---------------------------------------------------------------------------
# Title cleanup + URL source detection
# ---------------------------------------------------------------------------


_TIKTOK_SHOP_SUFFIX = re.compile(r"\s*[-–—]\s*tiktok\s*shop\s*$", re.IGNORECASE)
_LEADING_HASHTAGS = re.compile(r"^(?:\s*#\w+\s*){3,}")


def clean_product_title(title: str) -> str:
    """Best-effort cleanup of TikTok/Kalodata product titles.

    Removes a trailing ' - TikTok Shop' suffix and excessive leading
    hashtags. Collapses runs of whitespace. Preserves the rest verbatim
    — under-cleaning is better than over-cleaning here because the
    user might want details like flavor, size, or brand.
    """
    if not title:
        return ""
    cleaned = _TIKTOK_SHOP_SUFFIX.sub("", title)
    cleaned = _LEADING_HASHTAGS.sub("", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def detect_url_source(url: str) -> str:
    """Classify a product URL as 'TikTok', 'Kalodata', or 'Other'."""
    if not url:
        return "Other"
    u = url.lower()
    if "tiktok.com" in u:
        return "TikTok"
    if "kalodata.com" in u:
        return "Kalodata"
    return "Other"


# ---------------------------------------------------------------------------
# Batch CRUD
# ---------------------------------------------------------------------------


def make_batch_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def list_batches() -> list[str]:
    if not BATCHES_DIR.exists():
        return []
    return sorted(
        (p.name for p in BATCHES_DIR.iterdir() if p.is_dir()),
        reverse=True,
    )


def batch_dir(batch_id: str) -> Path:
    return BATCHES_DIR / batch_id


def create_batch(batch_id: Optional[str] = None) -> str:
    bid = batch_id or make_batch_id()
    d = batch_dir(bid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "reference_images").mkdir(exist_ok=True)
    products_file = d / "products.json"
    if not products_file.exists():
        products_file.write_text("[]\n", encoding="utf-8")
    return bid


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------


def load_products(batch_id: str) -> list[ProductCard]:
    f = batch_dir(batch_id) / "products.json"
    if not f.exists():
        return []
    raw = json.loads(f.read_text(encoding="utf-8") or "[]")
    out: list[ProductCard] = []
    for entry in raw:
        # Be lenient: extra/missing keys won't crash; defaults fill in.
        kwargs = {k: v for k, v in entry.items() if k in ProductCard.__dataclass_fields__}
        out.append(ProductCard(**kwargs))
    return out


def save_products(batch_id: str, products: Iterable[ProductCard]) -> None:
    d = batch_dir(batch_id)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "products.json"
    payload = json.dumps([asdict(p) for p in products], indent=2, ensure_ascii=False)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(f)


def new_product_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Reference-image storage
# ---------------------------------------------------------------------------


def save_reference_image(
    batch_id: str, product_id: str, role: str, data: bytes, ext: str
) -> Path:
    """Save uploaded bytes under data/batches/<batch_id>/reference_images/.

    role must be one of REFERENCE_ROLES (primary, ref2, ref3). ext can
    have or omit a leading dot.
    """
    if role not in REFERENCE_ROLES:
        raise ValueError(f"role must be one of {REFERENCE_ROLES}, got {role!r}")
    suffix = ext.lower().lstrip(".") or "jpg"
    target_dir = batch_dir(batch_id) / "reference_images"
    target_dir.mkdir(parents=True, exist_ok=True)
    # Remove any prior file with this product+role under a different ext
    for existing in target_dir.glob(f"{product_id}_{role}.*"):
        try:
            existing.unlink()
        except OSError:
            pass
    path = target_dir / f"{product_id}_{role}.{suffix}"
    path.write_bytes(data)
    return path


def reference_image_rel(path: Path) -> str:
    """Return a repo-relative posix path for storage in products.json."""
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return str(path)
    return rel.as_posix()


# ---------------------------------------------------------------------------
# Image pool — bulk-uploaded images that aren't yet tied to a product
# ---------------------------------------------------------------------------


def image_pool_dir(batch_id: str) -> Path:
    return batch_dir(batch_id) / "image_pool"


def list_image_pool(batch_id: str) -> list[Path]:
    d = image_pool_dir(batch_id)
    if not d.exists():
        return []
    return sorted(
        p for p in d.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def add_to_image_pool(batch_id: str, filename: str, data: bytes) -> Path:
    """Save bytes into the batch's image_pool with a unique name."""
    d = image_pool_dir(batch_id)
    d.mkdir(parents=True, exist_ok=True)
    target = d / filename
    if target.exists():
        stem, suffix = Path(filename).stem, Path(filename).suffix
        i = 1
        while (d / f"{stem}_{i}{suffix}").exists():
            i += 1
        target = d / f"{stem}_{i}{suffix}"
    target.write_bytes(data)
    return target


def attach_pool_image_to_product(
    batch_id: str,
    product: ProductCard,
    pool_image: Path,
    role: str,
) -> Path:
    """Copy a pool image into a product's reference_images slot.

    Caller is responsible for persisting `product.reference_images` to
    products.json afterwards. Returns the new reference image path.
    """
    if role not in REFERENCE_ROLES:
        raise ValueError(f"role must be one of {REFERENCE_ROLES}")
    ext = pool_image.suffix.lstrip(".") or "jpg"
    data = pool_image.read_bytes()
    saved = save_reference_image(batch_id, product.id, role, data, ext)
    # Replace any existing entry for this role; keep the order stable.
    rel = reference_image_rel(saved)
    new_refs: list[str] = []
    replaced = False
    for r in product.reference_images:
        if f"_{role}." in Path(r).name:
            new_refs.append(rel)
            replaced = True
        else:
            new_refs.append(r)
    if not replaced:
        new_refs.append(rel)
    product.reference_images = new_refs
    return saved


def remove_from_image_pool(pool_image: Path) -> None:
    try:
        pool_image.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Manifest export + sync to existing CLI locations
# ---------------------------------------------------------------------------


def export_manifest(
    batch_id: str,
    settings: Settings,
    *,
    sync_to_inputs: bool = True,
) -> dict:
    """Generate prompt_manifest.md from products.json and optionally sync.

    Returns a summary dict with paths and counts.

    Manifest section IDs are sequential 2-digit numbers (01, 02, ...)
    so the existing parser (which expects 1-3 digit IDs) keeps working.
    The internal `product.id` (8-hex UUID) stays in products.json for
    cross-reference but does NOT appear in the manifest.
    """
    products = load_products(batch_id)
    # Strict blanket mode: video_prompt is filled at export from the
    # universal prompt when a product lacks its own. Only image_prompt
    # is a hard requirement at the export gate.
    eligible = [
        p for p in products
        if p.is_ready_to_export() and p.primary_reference()
    ]

    # Resolve the blanket prompt once. The manifest parser requires a
    # Video Prompt section, so we always write SOMETHING — either the
    # product's own (advanced future use) or the blanket prompt.
    blanket = settings.blanket_video_prompt.rstrip()

    lines: list[str] = [
        f"<!-- generated from data/batches/{batch_id}/products.json -->",
        f"<!-- {datetime.now().isoformat(timespec='seconds')} -->",
        "",
    ]
    exported_uuids: list[str] = []
    for n, p in enumerate(eligible, start=1):
        primary = p.primary_reference() or ""
        primary_stem = Path(primary).stem
        section_id = f"{n:02d}"
        lines.append(f"## {section_id}")
        lines.append(f"<!-- product_uuid: {p.id} -->")
        lines.append(f"Product Name: {p.product_name}")
        lines.append(f"Reference Image: {primary_stem}")
        lines.append("Status: pending")
        lines.append("")
        lines.append("Image Prompt:")
        lines.append(p.image_prompt.rstrip())
        lines.append("")
        lines.append("Video Prompt:")
        if settings.use_blanket_video_prompt or not p.video_prompt.strip():
            lines.append(blanket)
        else:
            lines.append(p.video_prompt.rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")
        exported_uuids.append(p.id)

    batch_manifest_path = batch_dir(batch_id) / "prompt_manifest.md"
    batch_manifest_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "batch_id": batch_id,
        "exported_count": len(eligible),
        "skipped_count": len(products) - len(eligible),
        "batch_manifest_path": str(batch_manifest_path),
        "synced_to_inputs": False,
        "synced_manifest_path": None,
        "synced_reference_count": 0,
    }

    if sync_to_inputs:
        # Copy manifest to inputs/prompt_manifest.md (path from settings).
        settings.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(batch_manifest_path, settings.manifest_path)
        summary["synced_to_inputs"] = True
        summary["synced_manifest_path"] = str(settings.manifest_path)

        # Copy reference images so the resolver finds them by stem.
        settings.reference_images_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for p in eligible:
            for ref in p.reference_images:
                src = (REPO_ROOT / ref) if not Path(ref).is_absolute() else Path(ref)
                if not src.exists():
                    continue
                dst = settings.reference_images_dir / src.name
                shutil.copy2(src, dst)
                copied += 1
        summary["synced_reference_count"] = copied

    # Mark exported products as such (so the UI shows the green check).
    for p in products:
        if p.id in exported_uuids:
            p.status = PRODUCT_STATUS_EXPORTED
    save_products(batch_id, products)

    return summary
