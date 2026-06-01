"""Build BOF prompts from a Product.

v3 — AIBOF Image & Video Prompt Framework.

The universal fallback prompt below is used when the manual AI
provider is selected or when AI generation isn't run. It captures the
framework's core principles without per-product variable substitution
(no category-specific display method / store type / lighting). When
you use an AI provider, the system prompt in
``ai/providers/base.py`` produces a fully filled-in version of the
template instead.

Per-row ``prompt_override`` always wins.
"""

from __future__ import annotations

from .utils import Product


UNIVERSAL_BOF_PROMPT = (
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


def build_prompt(product: Product) -> str:
    if product.prompt_override:
        return product.prompt_override
    return UNIVERSAL_BOF_PROMPT
