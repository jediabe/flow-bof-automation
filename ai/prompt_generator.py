"""Top-level AI provider factory + output validation.

Usage:
    provider = get_provider()        # reads AI_PROVIDER env, default = manual
    ok, msg = provider.is_configured()
    if ok:
        result = provider.generate_product_prompts(product_dict)
        ok2, problems = validate_ai_output(result)
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from .providers.base import (
    AIProvider,
    REQUIRED_OUTPUT_KEYS,
    SUPPORTED_OUTPUT_KEYS,
)


KNOWN_PROVIDERS = ("openai", "anthropic", "openrouter", "manual")


def get_provider(name: Optional[str] = None) -> AIProvider:
    name = (name or os.environ.get("AI_PROVIDER") or "manual").strip().lower()
    if name == "openai":
        from .providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if name == "anthropic":
        from .providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if name == "openrouter":
        from .providers.openrouter_provider import OpenRouterProvider
        return OpenRouterProvider()
    if name == "manual":
        from .providers.manual_provider import ManualProvider
        return ManualProvider()
    raise ValueError(f"Unknown AI provider: {name!r}. Choose one of {KNOWN_PROVIDERS}.")


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model response.

    Handles three cases the LLM might emit:
      1. Pure JSON: parse directly.
      2. JSON wrapped in ```json ... ``` fences: strip them.
      3. JSON embedded in commentary: take the first {...} block.
    Raises json.JSONDecodeError on real failure.
    """
    text = (text or "").strip()
    if not text:
        raise json.JSONDecodeError("empty response", "", 0)

    # Case 2: code fence.
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass  # fall through

    # Case 1: direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Case 3: take the outermost {...}.
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        return json.loads(text[first : last + 1])

    raise json.JSONDecodeError("no JSON object found in response", text, 0)


_TEST_PROMPT = 'Return strict JSON only: {"ok": true}. No commentary, no prose, no markdown.'


def test_ai_provider(
    provider_name: str,
    model: str = "",
    api_key: str = "",
) -> tuple[bool, str]:
    """Make a tiny test call against the given provider.

    Tries to verify that the API key works and (if specified) the model
    name is accepted. Uses ~10-20 tokens — cheap to run.

    The optional ``model``/``api_key`` arguments let the UI test a value
    before saving it without permanently changing os.environ. If empty,
    falls back to whatever is currently in os.environ.

    Returns (ok, message). The message is short and safe to display.
    """
    name = (provider_name or "").strip().lower()
    if name == "manual":
        return True, "Manual provider — no API key required."

    if name == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return False, "OPENAI_API_KEY is empty."
        m = (model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=m,
                messages=[{"role": "user", "content": _TEST_PROMPT}],
                max_tokens=20,
                temperature=0,
            )
            content = (resp.choices[0].message.content or "").strip()
            return True, f"OK (model {m}). Reply: {content[:60]}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:200]}"

    if name == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return False, "ANTHROPIC_API_KEY is empty."
        m = (model or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest").strip()
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=key)
            msg = client.messages.create(
                model=m,
                max_tokens=30,
                messages=[{"role": "user", "content": _TEST_PROMPT}],
            )
            text = "".join(getattr(b, "text", "") for b in msg.content).strip()
            return True, f"OK (model {m}). Reply: {text[:60]}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:200]}"

    if name == "openrouter":
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return False, "OPENROUTER_API_KEY is empty."
        m = (model or os.environ.get("OPENROUTER_MODEL") or "").strip()
        warn_auto = False
        if not m:
            m = "openrouter/auto"
            warn_auto = True
        try:
            from openai import OpenAI
            client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
            resp = client.chat.completions.create(
                model=m,
                messages=[{"role": "user", "content": _TEST_PROMPT}],
                max_tokens=20,
                temperature=0,
            )
            content = (resp.choices[0].message.content or "").strip()
            note = " (using openrouter/auto — set OPENROUTER_MODEL to lock a specific model)" if warn_auto else ""
            return True, f"OK (model {m}){note}. Reply: {content[:60]}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:200]}"

    return False, f"Unknown provider: {provider_name!r}. Choose one of {KNOWN_PROVIDERS}."


def validate_ai_output(data: dict) -> tuple[bool, list[str]]:
    """Validate the schema of an AI response.

    Returns (ok, problems). Even when ok=False the data may still be
    partially usable — the UI should show problems and let the user
    edit before saving.

    Under strict blanket-video-prompt mode (USE_BLANKET_VIDEO_PROMPT=true,
    the default), ``video_prompt`` is downgraded to optional: the
    manifest export and video generation both substitute the universal
    blanket prompt. If the model returns one anyway it's stored on the
    card for future advanced use, just not validated against.
    """
    if not isinstance(data, dict):
        return False, ["AI response is not a JSON object"]

    use_blanket = (os.environ.get("USE_BLANKET_VIDEO_PROMPT", "true") or "true").strip().lower() not in {
        "0", "false", "no", "n", "off", ""
    }
    required = set(REQUIRED_OUTPUT_KEYS)
    if use_blanket:
        required.discard("video_prompt")

    problems: list[str] = []
    for key in required:
        value = data.get(key)
        if not (isinstance(value, str) and value.strip()):
            problems.append(f"missing or empty required key: {key}")
    unexpected = set(data.keys()) - SUPPORTED_OUTPUT_KEYS
    for key in sorted(unexpected):
        problems.append(f"unexpected key: {key}")
    return len(problems) == 0, problems
