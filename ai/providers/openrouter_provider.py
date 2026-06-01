"""OpenRouter provider — OpenAI-compatible API at openrouter.ai."""

from __future__ import annotations

import os

from .base import AIProvider, format_user_prompt, get_system_prompt


class OpenRouterProvider(AIProvider):
    @property
    def name(self) -> str:
        return "openrouter"

    def is_configured(self) -> tuple[bool, str]:
        if not os.environ.get("OPENROUTER_API_KEY"):
            return False, "OPENROUTER_API_KEY not set"
        return True, "ok"

    def generate_product_prompts(self, product: dict) -> dict:
        ok, msg = self.is_configured()
        if not ok:
            raise RuntimeError(msg)

        # OpenRouter speaks the OpenAI SDK — same client, different
        # base_url + auth. Pass referer/title headers OpenRouter uses
        # for attribution.
        from openai import OpenAI

        extra_headers = {}
        if os.environ.get("OPENROUTER_SITE_URL"):
            extra_headers["HTTP-Referer"] = os.environ["OPENROUTER_SITE_URL"]
        if os.environ.get("OPENROUTER_APP_NAME"):
            extra_headers["X-Title"] = os.environ["OPENROUTER_APP_NAME"]

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            default_headers=extra_headers or None,
        )

        # OPENROUTER_MODEL may be intentionally blank — the user wants
        # OpenRouter's auto-router to pick. The OpenAI SDK requires a
        # non-empty `model` value, so we map blank -> 'openrouter/auto'
        # which is OpenRouter's documented auto-routing model.
        model = (os.environ.get("OPENROUTER_MODEL") or "").strip()
        if not model:
            model = "openrouter/auto"

        resp = client.chat.completions.create(
            model=model,
            temperature=0.4,
            messages=[
                {"role": "system", "content": get_system_prompt() + "\n\nReturn JSON only."},
                {"role": "user", "content": format_user_prompt(product)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        from ..prompt_generator import extract_json
        return extract_json(content)
