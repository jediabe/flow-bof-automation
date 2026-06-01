"""Top-level orchestration: loop over products, build prompts, drive Flow Labs."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Settings
from .flow_automation import (
    FlowAutomationError,
    generate_image_for_product,
    open_flow_browser,
)
from .prompt_builder import build_prompt
from .utils import (
    Product,
    load_products,
    next_output_paths,
    output_dir_for,
    validate_product_image,
)


@dataclass
class BatchOptions:
    dry_run: bool = False
    limit: int | None = None
    product_index: int | None = None


def _select_products(products: list[Product], opts: BatchOptions) -> list[Product]:
    if opts.product_index is not None:
        if opts.product_index < 0 or opts.product_index >= len(products):
            raise IndexError(
                f"--product-index {opts.product_index} out of range (0..{len(products) - 1})"
            )
        return [products[opts.product_index]]
    if opts.limit is not None:
        return products[: opts.limit]
    return products


def run_batch(settings: Settings, logger: logging.Logger, opts: BatchOptions) -> int:
    products = load_products(settings)
    chosen = _select_products(products, opts)
    logger.info("Selected %d of %d products", len(chosen), len(products))

    if opts.dry_run:
        return _run_dry(chosen, logger)

    return _run_live(settings, chosen, logger)


def _run_dry(products: list[Product], logger: logging.Logger) -> int:
    failures = 0
    for i, product in enumerate(products):
        prompt = build_prompt(product)
        print(f"\n=== [{i}] {product.product_name} ({product.category}) ===")
        print(f"Image: {product.product_image_path}")
        try:
            validate_product_image(product)
            print("Image exists: yes")
        except FileNotFoundError as exc:
            failures += 1
            print(f"Image exists: NO — {exc}")
            logger.warning("Missing image for %s", product.product_name)
        print(f"Store: {product.store}")
        print(f"Section: {product.section}")
        print(f"Placement: {product.placement_type}")
        print("\nPrompt:")
        print(prompt)
    if failures:
        logger.warning("Dry run found %d missing image(s)", failures)
    return 1 if failures else 0


def _run_live(settings: Settings, products: list[Product], logger: logging.Logger) -> int:
    for product in products:
        validate_product_image(product)

    failures = 0
    with open_flow_browser(settings, logger) as session:
        for i, product in enumerate(products):
            logger.info("--- [%d/%d] %s", i + 1, len(products), product.product_name)
            prompt = build_prompt(product)
            out_dir = output_dir_for(settings, product)
            image_path, prompt_path = next_output_paths(out_dir, product.slug)
            prompt_path.write_text(prompt, encoding="utf-8")
            try:
                generate_image_for_product(
                    session=session,
                    settings=settings,
                    product=product,
                    prompt=prompt,
                    output_image=image_path,
                    logger=logger,
                )
            except FlowAutomationError as exc:
                failures += 1
                logger.error("Generation failed for %s: %s", product.product_name, exc)
            except Exception:  # noqa: BLE001
                failures += 1
                logger.exception("Unexpected error for %s", product.product_name)
    return 1 if failures else 0
