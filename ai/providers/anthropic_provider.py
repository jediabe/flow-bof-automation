"""Anthropic provider — Messages API. Forces strict JSON via system prompt."""

from __future__ import annotations

import os

from .base import AIProvider, format_user_prompt, get_system_prompt


class AnthropicProvider(AIProvider):
    @property
    def name(self) -> str:
        return "anthropic"

    def is_configured(self) -> tuple[bool, str]:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY not set"
        return True, "ok"

    def generate_product_prompts(self, product: dict) -> dict:
        ok, msg = self.is_configured()
        if not ok:
            raise RuntimeError(msg)

        from anthropic import Anthropic

        client = Anthropic()
        model = (
            os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest").strip()
            or "claude-3-5-sonnet-latest"
        )

        message = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0.4,
            # Anthropic has no native JSON mode; the system prompt
            # already demands strict JSON, but we double-reinforce.
            system=get_system_prompt() + "\n\nReturn JSON only. No markdown.",
            messages=[
                {"role": "user", "content": format_user_prompt(product)},
            ],
        )
        # `content` is a list of blocks; concatenate text blocks.
        chunks: list[str] = []
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
        from ..prompt_generator import extract_json
        return extract_json("".join(chunks))
