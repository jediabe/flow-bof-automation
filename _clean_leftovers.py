"""Throwaway cleanup: drop rows with no manifest id (leftover scan-flow rows)."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.config import load_settings
from src.csv_workflow import load_csv, save_csv

settings = load_settings()
rows = load_csv(settings.products_csv)
kept, dropped = [], []
for r in rows:
    if r.id.strip():
        kept.append(r)
    else:
        dropped.append(r)

print(f"Dropping {len(dropped)} row(s) with empty id:")
for r in dropped:
    print(f"  - {r.product_name}")
print(f"Keeping {len(kept)} manifest row(s):")
for r in kept:
    print(f"  + id={r.id}  {r.product_name}  (media_id={r.flow_media_id})")

save_csv(settings.products_csv, kept)
print(f"\nWrote {settings.products_csv}")
