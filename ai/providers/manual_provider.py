"""Manual provider — no API calls. The UI lets the user edit prompts."""

from __future__ import annotations

from .base import AIProvider


class ManualProvider(AIProvider):
    @property
    def name(self) -> str:
        return "manual"

    def is_configured(self) -> tuple[bool, str]:
        return True, "ok (manual entry — no API)"

    def generate_product_prompts(self, product: dict) -> dict:
        return {
            "product_name": product.get("product_name", ""),
            "category": product.get("category_hint", "") or product.get("category", ""),
            "store_environment": "",
            "placement_type": "",
            "image_prompt": "",
            "video_prompt": "",
            "hook": "",
            "caption": "",
            "warnings": ["Manual provider selected — fill in prompts yourself."],
        }
