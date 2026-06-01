"""Tiny on-disk store of media_ids already submitted for video.

Used to de-dup across runs of ``--generate-videos`` so the user doesn't
re-animate the same favorited tile every time they re-hit the button.

File: ``data/video_submitted_tiles.json``

Shape:
    {
      "media_ids": ["xyz", "abc", ...],
      "history": [
        {"media_id": "xyz", "submitted_at": "2026-06-01T18:22:31"},
        ...
      ]
    }

Atomic writes via tempfile + os.replace.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT


STATE_FILE = REPO_ROOT / "data" / "video_submitted_tiles.json"


def _load_raw() -> dict:
    if not STATE_FILE.exists():
        return {"media_ids": [], "history": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return {"media_ids": [], "history": []}
    if not isinstance(data, dict):
        return {"media_ids": [], "history": []}
    data.setdefault("media_ids", [])
    data.setdefault("history", [])
    if not isinstance(data["media_ids"], list):
        data["media_ids"] = []
    if not isinstance(data["history"], list):
        data["history"] = []
    return data


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_submitted_media_ids() -> set[str]:
    """Return the set of media_ids that have been submitted for video."""
    return {m for m in _load_raw().get("media_ids", []) if isinstance(m, str) and m}


def mark_submitted(media_id: str) -> None:
    """Record a successful video submission for the given media_id."""
    if not media_id:
        return
    data = _load_raw()
    if media_id not in data["media_ids"]:
        data["media_ids"].append(media_id)
    data["history"].append({
        "media_id": media_id,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
    })
    _atomic_write(STATE_FILE, data)


def clear_submitted() -> None:
    """Reset the store. Used by the UI 'Include already submitted' path
    when the user wants to re-submit everything."""
    _atomic_write(STATE_FILE, {"media_ids": [], "history": []})
