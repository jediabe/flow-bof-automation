"""Interactive recovery: bind favorited Flow tiles to CSV rows.

Scans the live Flow project, lists every favorited tile with its
click-to-open URL, and prompts you for each one. You enter the CSV id
(01, 02, …) the tile belongs to. The script then rewrites
inputs/products.csv with the corrected flow_media_id values.

Run when the auto-captured media_id ended up pointing at a different
variant than the one you hearted.
"""
from __future__ import annotations
import csv
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_settings
from src.flow_automation import acquire_flow_page, open_flow_browser
from src.flow_tiles import scan_tiles
from src.utils import setup_logging

settings = load_settings()
logger = setup_logging(settings)

with open_flow_browser(settings, logger) as session:
    page = acquire_flow_page(session, settings, logger)
    tiles = scan_tiles(page)

favorited = [t for t in tiles if t.favorited and t.flow_media_id]
print(f"\nFound {len(favorited)} favorited tile(s) on this Flow project.\n")
if not favorited:
    print("Nothing to remap.")
    raise SystemExit(0)

# Show current image_submitted rows that are missing the right tile.
csv_path = settings.products_csv
with csv_path.open(encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

print("CSV rows at status=image_submitted:")
for r in rows:
    if r["status"] == "image_submitted":
        media = (r.get("flow_media_id") or "")[:8] or "(empty)"
        print(f"  id={r['id']:4}  current media={media}  {r['product_name'][:60]}")
print()

base = "https://labs.google"
assignments: dict[str, str] = {}  # media_id -> row id

for i, t in enumerate(favorited, 1):
    href = t.tile_href if t.tile_href.startswith("http") else f"{base}{t.tile_href}"
    print(f"\n--- Favorited tile {i}/{len(favorited)} ---")
    print(f"  media_id: {t.flow_media_id}")
    print(f"  open:     {href}")
    try:
        row_id = input("  Which CSV id does this tile belong to? (blank = skip): ").strip()
    except EOFError:
        row_id = ""
    if row_id:
        assignments[t.flow_media_id] = row_id

if not assignments:
    print("\nNo assignments made. Nothing written.")
    raise SystemExit(0)

updated = 0
for r in rows:
    for media_id, target_id in assignments.items():
        if r["id"] == target_id:
            r["flow_media_id"] = media_id
            updated += 1

fd, tmp = tempfile.mkstemp(prefix="products.csv.", suffix=".tmp", dir=str(csv_path.parent))
with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r in rows:
        w.writerow(r)
os.replace(tmp, csv_path)

print(f"\nUpdated {updated} row(s) in {csv_path}.")
print("Next: python main.py --sync-favorites")
