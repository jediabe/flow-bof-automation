"""OpenAI provider — chat.completions with response_format=json_object."""

from __future__ import annotations

import json
import os

from .base import AIProvider, SYSTEM_PROMPT, format_user_prompt


class OpenAIProvider(AIProvider):
    @property
    def name(self) -> str:
        return "openai"

    def is_configured(self) -> tuple[bool, str]:
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY not set"
        return True, "ok"

    def generate_product_prompts(self, product: dict) -> dict:
        ok, msg = self.is_configured()
        if not ok:
            raise RuntimeError(msg)

        # Import inside method so the module can be loaded even when
        # `openai` isn't installed (e.g. when the user only ever uses
        # the manual provider).
        from openai import OpenAI

        client = OpenAI()
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0.4,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": format_user_prompt(product)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return json.loads(content)
