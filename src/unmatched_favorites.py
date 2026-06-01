"""Persistent record of favorited Flow tiles that couldn't be auto-bound.

When the user manually regenerates an image variant in Flow and then
favorites it, the new media_id won't appear in any CSV row's
``flow_media_id`` list. Sync favorites can't safely guess which product
that tile belongs to without more context, so it stashes the tile here
and lets the user bind it from the UI (or via CLI).

State file:
    data/unmatched_favorites.json

This lives at the project root (next to ``inputs/``), not per batch —
the sync command is global (operates on inputs/products.csv) and
doesn't know which batch produced the current CSV.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT


UNMATCHED_FILE = REPO_ROOT / "data" / "unmatched_favorites.json"


@dataclass
class UnmatchedFavorite:
    media_id: str = ""
    flow_tile_id: str = ""
    flow_image_src: str = ""
    tile_href: str = ""
    edit_id: str = ""
    detected_at: str = ""

    def normalized(self) -> "UnmatchedFavorite":
        return UnmatchedFavorite(
            media_id=self.media_id.strip(),
            flow_tile_id=self.flow_tile_id.strip(),
            flow_image_src=self.flow_image_src.strip(),
            tile_href=self.tile_href.strip(),
            edit_id=self.edit_id.strip(),
            detected_at=self.detected_at.strip() or datetime.now().isoformat(timespec="seconds"),
        )


def load_unmatched() -> list[UnmatchedFavorite]:
    if not UNMATCHED_FILE.exists():
        return []
    try:
        raw = json.loads(UNMATCHED_FILE.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    out: list[UnmatchedFavorite] = []
    field_names = {f.name for f in fields(UnmatchedFavorite)}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kwargs = {k: v for k, v in entry.items() if k in field_names}
        out.append(UnmatchedFavorite(**kwargs))
    return out


def save_unmatched(items: list[UnmatchedFavorite]) -> None:
    UNMATCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([asdict(i) for i in items], indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(
        prefix=UNMATCHED_FILE.name + ".",
        suffix=".tmp",
        dir=str(UNMATCHED_FILE.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(payload + "\n")
        os.replace(tmp_name, UNMATCHED_FILE)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def merge_unmatched(new_items: list[UnmatchedFavorite]) -> list[UnmatchedFavorite]:
    """Add `new_items` to the on-disk state, dedup on media_id."""
    existing = load_unmatched()
    by_id = {i.media_id: i for i in existing if i.media_id}
    for item in new_items:
        if not item.media_id:
            continue
        normalized = item.normalized()
        # Update with newer metadata, but keep the first detected_at.
        prior = by_id.get(normalized.media_id)
        if prior and prior.detected_at:
            normalized.detected_at = prior.detected_at
        by_id[normalized.media_id] = normalized
    merged = list(by_id.values())
    save_unmatched(merged)
    return merged


def remove_unmatched(media_id: str) -> bool:
    """Drop the entry matching media_id. Returns True if anything was removed."""
    existing = load_unmatched()
    kept = [i for i in existing if i.media_id != media_id]
    if len(kept) == len(existing):
        return False
    save_unmatched(kept)
    return True


def clear_unmatched() -> None:
    save_unmatched([])
