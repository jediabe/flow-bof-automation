"""Throwaway: connect to live Chrome and print every visible tile with
its media_id + tile_href so the user can manually map them to CSV rows.
"""
from __future__ import annotations
import sys
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
    tiles = scan_tiles(page, logger=logger)

print()
print(f"=== {len(tiles)} tiles found ===\n")
for i, t in enumerate(tiles, 1):
    fav = "[♥]" if t.favorited else "[ ]"
    print(f"{fav} tile {i}")
    print(f"      media_id: {t.flow_media_id}")
    print(f"      tile_href: {t.tile_href}")
    print()

print("To map a tile to a CSV row:")
print("  1. Copy the tile_href, paste into your browser.")
print("  2. See which product image it is.")
print("  3. Set that row's flow_media_id in inputs/products.csv.")
