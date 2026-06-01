"""Logging, filesystem helpers, and product loading."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import CATEGORY_RULES, Settings


@dataclass
class Product:
    product_name: str
    category: str
    product_image_path: Path
    store: str
    placement_type: str
    section: str
    prompt_override: str | None

    @property
    def slug(self) -> str:
        return slugify(self.product_name)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "product"


def load_products(settings: Settings) -> list[Product]:
    if not settings.products_json.exists():
        raise FileNotFoundError(f"Missing products file: {settings.products_json}")

    raw = json.loads(settings.products_json.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("products.json must be a JSON array")

    products: list[Product] = []
    for i, entry in enumerate(raw):
        products.append(product_from_dict(entry, settings, index=i))
    return products


def product_from_dict(entry: dict[str, Any], settings: Settings, index: int = 0) -> Product:
    name = entry.get("product_name")
    if not name:
        raise ValueError(f"products[{index}] is missing product_name")

    category = (entry.get("category") or "misc").strip().lower()
    rules = CATEGORY_RULES.get(category, CATEGORY_RULES["misc"])

    image_raw = entry.get("product_image_path")
    if not image_raw:
        raise ValueError(f"products[{index}] ({name}) is missing product_image_path")
    image_path = Path(image_raw)
    if not image_path.is_absolute():
        image_path = settings.repo_root / image_path

    return Product(
        product_name=name,
        category=category,
        product_image_path=image_path,
        store=entry.get("store") or rules["store"],
        placement_type=entry.get("placement_type") or rules["placement_type"],
        section=entry.get("section") or rules["section"],
        prompt_override=entry.get("prompt_override"),
    )


def validate_product_image(product: Product) -> None:
    if not product.product_image_path.exists():
        raise FileNotFoundError(
            f"Image not found for {product.product_name!r}: {product.product_image_path}"
        )


def ensure_dirs(settings: Settings) -> None:
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)


def setup_logging(settings: Settings) -> logging.Logger:
    ensure_dirs(settings)
    log_path = settings.logs_dir / f"{datetime.now():%Y-%m-%d}.log"

    logger = logging.getLogger("flow_bof")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    return logger


def output_dir_for(settings: Settings, product: Product, day: datetime | None = None) -> Path:
    day = day or datetime.now()
    path = settings.images_dir / day.strftime("%Y-%m-%d") / product.slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_output_paths(out_dir: Path, slug: str, ext: str = "png") -> tuple[Path, Path]:
    """Return (image_path, prompt_text_path) using a 3-digit counter."""
    n = 1
    while True:
        img = out_dir / f"{slug}_{n:03d}.{ext}"
        if not img.exists():
            return img, out_dir / f"{slug}_{n:03d}.prompt.txt"
        n += 1
