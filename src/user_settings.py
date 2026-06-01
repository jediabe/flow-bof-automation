"""UI-managed local settings + API key storage.

Two files in ``data/``:

  settings.local.json — non-secret config: AI provider, model names,
                        OpenRouter site URL / app name.
  secrets.local.json   — API keys. **Excluded from git + Docker contexts
                        via .gitignore / .dockerignore.**

Loading priority for any value the AI providers care about:

  1. UI-saved local settings/secrets (these files).
  2. Environment variables (docker-compose env / .env / shell).
  3. Hard-coded defaults inside the provider class.

The Streamlit UI calls :func:`save_settings` / :func:`save_secrets` and
immediately calls :func:`apply_to_env` so subsequent provider calls see
the new values. The CLI calls :func:`apply_to_env` once at startup so
``--generate-prompts`` and friends also pick up UI-saved values.

The provider classes read from ``os.environ`` only — applying UI values
to ``os.environ`` keeps them backwards-compatible with no signature
changes.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import REPO_ROOT


SETTINGS_FILE = REPO_ROOT / "data" / "settings.local.json"
SECRETS_FILE = REPO_ROOT / "data" / "secrets.local.json"


@dataclass
class UserSettings:
    ai_provider: str = ""
    openai_model: str = ""
    anthropic_model: str = ""
    openrouter_model: str = ""
    openrouter_site_url: str = ""
    openrouter_app_name: str = ""
    # Strict blanket video prompt. Empty string means "fall through to
    # env / hard-coded default" — see config.DEFAULT_BLANKET_VIDEO_PROMPT.
    use_blanket_video_prompt: str = ""  # "" / "true" / "false"
    blanket_video_prompt: str = ""


@dataclass
class UserSecrets:
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""


# Map UserSettings/UserSecrets field name -> env var name. Used by
# apply_to_env to push values into os.environ.
_SETTINGS_TO_ENV = {
    "ai_provider":             "AI_PROVIDER",
    "openai_model":            "OPENAI_MODEL",
    "anthropic_model":         "ANTHROPIC_MODEL",
    "openrouter_model":        "OPENROUTER_MODEL",
    "openrouter_site_url":     "OPENROUTER_SITE_URL",
    "openrouter_app_name":     "OPENROUTER_APP_NAME",
    "use_blanket_video_prompt":"USE_BLANKET_VIDEO_PROMPT",
    "blanket_video_prompt":    "BLANKET_VIDEO_PROMPT",
}
_SECRETS_TO_ENV = {
    "openai_api_key":      "OPENAI_API_KEY",
    "anthropic_api_key":   "ANTHROPIC_API_KEY",
    "openrouter_api_key":  "OPENROUTER_API_KEY",
}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_dataclass(cls, path: Path):
    if not path.exists():
        return cls()
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return cls()
    if not isinstance(raw, dict):
        return cls()
    allowed = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in raw.items() if k in allowed and isinstance(v, str)}
    return cls(**kwargs)


def load_settings() -> UserSettings:
    return _load_dataclass(UserSettings, SETTINGS_FILE)


def load_secrets() -> UserSecrets:
    return _load_dataclass(UserSecrets, SECRETS_FILE)


def save_settings(s: UserSettings) -> None:
    _atomic_write_json(SETTINGS_FILE, asdict(s))


def save_secrets(s: UserSecrets) -> None:
    _atomic_write_json(SECRETS_FILE, asdict(s))


def apply_to_env(
    settings: UserSettings | None = None,
    secrets: UserSecrets | None = None,
) -> dict[str, str]:
    """Push UI-saved values into os.environ for the current process.

    Returns a dict ``{ENV_VAR: source}`` where source is one of:
        ``"user"``      — overwritten from UI-saved settings/secrets.
        ``"env"``       — kept from existing os.environ value.
        ``"default"``   — neither user nor env had a non-empty value.

    The UI is priority 1, so non-empty user values overwrite env.
    Empty user values leave env / defaults alone.
    """
    if settings is None:
        settings = load_settings()
    if secrets is None:
        secrets = load_secrets()

    sources: dict[str, str] = {}
    for attr, env_key in {**_SETTINGS_TO_ENV, **_SECRETS_TO_ENV}.items():
        user_value = getattr(
            secrets if env_key in _SECRETS_TO_ENV.values() else settings,
            attr,
            "",
        )
        user_value = (user_value or "").strip()
        if user_value:
            os.environ[env_key] = user_value
            sources[env_key] = "user"
        elif (os.environ.get(env_key) or "").strip():
            sources[env_key] = "env"
        else:
            sources[env_key] = "default"
    return sources


def mask_key(key: str | None) -> str:
    """Display-only redaction. Never returns the full key."""
    if not key:
        return "(empty)"
    k = key.strip()
    if len(k) <= 8:
        return "***"
    return f"...{k[-4:]}"
