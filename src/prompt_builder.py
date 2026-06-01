"""Build BOF prompts from a Product.

Two universal fallbacks live here: one US (the editorial iPhone-15-Pro
retail template) and one UK (the Apex Initiative one-sentence retail
template). Used when the manual AI provider is selected or when AI
generation isn't run. ``build_prompt`` picks based on MARKET env.

When you use an AI provider, the system prompt in ``ai/providers/base.py``
produces a fully filled-in version of the template instead, also
keyed off MARKET.

Per-row ``prompt_override`` always wins.
"""

from __future__ import annotations

import os

from .utils import Product


# US universal fallback — the original AIBOF editorial template,
# generic across categories so it works without an AI provider.
UNIVERSAL_BOF_PROMPT_US = (
    "Editorial retail product shot of the product displayed exactly as "
    "shown in the reference image on a retail display inside a modern "
    "big-box retail store that matches the product type. Match the "
    "product's color, texture, size, and details precisely as they "
    "appear in the reference. The product is the clear hero focus with "
    "open negative space surrounding it, nothing else nearby. No store "
    "logos, no brand signage, no price tags visible anywhere.\n\n"
    "Bright clean overhead retail lighting combined with soft ambient "
    "store lighting. Background softly blurred with realistic retail "
    "shelving and store atmosphere visible in the distance.\n\n"
    "Shot on a handheld iPhone 15 Pro style camera with authentic "
    "casual shopper framing and slight natural imperfections. Visible "
    "realism: realistic textures, slight dust particles catching light, "
    "natural shadows, true-to-size proportions. Not cinematic, not "
    "studio lighting, not glossy CGI, not overly polished. Looks like a "
    "real customer discovered the viral TikTok Shop deal while browsing."
)


# UK universal fallback — full four-paragraph template (reference
# guardrail + product extraction + master placement sentence + realism
# constraints). Used when the manual AI provider is selected or no AI
# provider has been run. When an AI provider IS running it produces a
# per-product version with the right UK retailer baked in; this
# fallback uses the generic "UK retail store" sentence so it works for
# any product.
UNIVERSAL_BOF_PROMPT_UK = (
    "Use the uploaded reference image only to understand the product's "
    "design. Do not copy the reference image layout, background, text, "
    "labels, promotional graphics, multiple variants, collage "
    "arrangement, or catalog composition.\n\n"
    "Extract the primary product as one realistic physical product "
    "display. Show one product, or one complete pair/set if that is "
    "how the product is naturally sold.\n\n"
    "Put a display setup for this product inside of a UK retail "
    "store, no price tags.\n\n"
    "Preserve the product's core shape, color, material, proportions, "
    "visible details, packaging, and branding if present. Make it look "
    "physically present in the store with realistic scale, contact "
    "shadows, shelf/table placement, and ordinary nearby store items. "
    "Casual handheld shopper photo, realistic UK retail environment. "
    "No text overlays, no promotional graphics, no catalog layout, no "
    "studio render."
)


# Back-compat alias. Old callers used UNIVERSAL_BOF_PROMPT directly;
# they now get the US default by default. New callers should use
# universal_bof_prompt() which respects MARKET.
UNIVERSAL_BOF_PROMPT = UNIVERSAL_BOF_PROMPT_US


def universal_bof_prompt(market: str | None = None) -> str:
    """Pick the universal fallback for the active market."""
    m = (market or os.environ.get("MARKET") or "US").strip().upper()
    if m == "UK":
        return UNIVERSAL_BOF_PROMPT_UK
    return UNIVERSAL_BOF_PROMPT_US


def build_prompt(product: Product) -> str:
    if product.prompt_override:
        return product.prompt_override
    return universal_bof_prompt()
