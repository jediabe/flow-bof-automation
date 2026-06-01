# Quickstart — running your first batch

Assumes you've already done the five steps in [`README_FIRST.md`](../README_FIRST.md):
Docker installed, `setup.ps1` run, `start.ps1` run, logged into Flow, and an AI
key saved in Setup.

## Visual map of the workflow

```
Setup (one-time)
    │
    ▼
BOF Batch Builder ─── single page, runs the whole pipeline
    │
    ├─ 1. Pick or create a batch
    ├─ 2. Add products  (paste Kalodata export, paste TikTok URL, or type)
    ├─ 3. Drop reference images
    ├─ 4. Generate AI prompts          ← uses your API key
    ├─ 5. Export manifest              ← writes inputs/prompt_manifest.md
    ├─ 6. Generate images in Flow      ← runs CLI inside Docker, drives Chrome
    ├─ 7. Heart favorites in Flow, then Sync Favorites
    └─ 8. Generate videos
```

## Step-by-step the first time

### 1. Create a batch
Click **Create first batch** when prompted. The batch is a folder in
`data/batches/<batch_id>/` — your products and prompts live there.

### 2. Add products
Easiest path: open Kalodata, click **Export** on your favorited list, drop
the `.xlsx` file into the **Bulk import** uploader. Each row becomes a product
card.

Other entry paths:
- Paste a TikTok Shop URL → the tool fetches the title/description.
- Type a name + description manually.

### 3. Reference images
For each product, drag in 1-3 product photos. These are the visual ground
truth the AI prompt will tell Flow to match.
- Screenshots from TikTok work fine.
- Clipboard paste also works (button under each product card).

### 4. Generate AI prompts
Click **Generate AI prompts for all products with images**. The tool sends
your product name + description + reference filenames to the AI provider you
configured, gets back image and video prompts, and saves them on each card.

If you picked **manual**, no API call happens; you fill the prompt fields
yourself. Or paste anything you wrote elsewhere.

### 5. Export manifest
Click **Export Manifest**. This writes `inputs/prompt_manifest.md` with one
`## NN` section per product. This is the file the Flow generator reads.

### 6. Generate images
Click **Generate Images (limit 30)**. Inside Docker, the CLI:
1. Connects to your real Chrome over CDP.
2. Opens Flow Labs.
3. For each product: creates a new project, uploads the reference image,
   pastes the prompt, hits Generate, captures the tile.

Watch the Chrome window — you'll see Flow generating in real time.

### 7. Heart favorites + sync
When Flow finishes, click the heart on every image you like. Then back in
the UI click **Sync Favorites**. The tool maps your hearts back to products
and marks them `image_approved`. Anything unmatched shows up in the
**Unmatched Favorited Images** section — bind to a product with one click.

### 8. Generate videos
Click **Generate Videos**. The tool picks each approved image, opens the
tile menu, and animates it — using the **same universal blanket prompt for
every product**. The Generate Videos section shows that prompt in an
editable text area; saves persist to `data/settings.local.json`.

Why one prompt for all? When you regenerate an image or manually heart an
alternate variant, a product-specific video prompt that was authored
against the original image can drift wildly. The blanket prompt is
designed to be safe across any product: a slow handheld push-in with a
single hand-tap, no morphing, no dramatic moves. Per-product video prompts
are still stored on each card and in the manifest, but they're reserved
for a future "advanced" mode and ignored at video time today.

To opt out (not recommended for the alpha): set
`USE_BLANKET_VIDEO_PROMPT=false` in `.env` and restart the UI.

## What happens to my outputs?

Generated content stays in Flow. The tool tracks tile IDs and edit URLs in
the CSV at `inputs/products.csv` so you can find the originals.

## Where things live

| Path                          | What it is                                     |
| ----------------------------- | ---------------------------------------------- |
| `data/batches/`               | Your product cards + prompts (one folder each) |
| `data/secrets.local.json`     | Your API key (never committed, never shipped)  |
| `data/settings.local.json`    | Your provider + model choice                   |
| `inputs/reference_images/`    | Photos you drop into the UI                    |
| `inputs/products.csv`         | Run state (status, media IDs, tile IDs)        |
| `inputs/prompt_manifest.md`   | The file the Flow CLI reads                    |
| `outputs/logs/`               | One log file per CLI run                       |
