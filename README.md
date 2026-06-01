# flow-bof-automation

Playwright automation that drives [Google Flow Labs](https://labs.google/flow) to generate bottom-of-funnel (BOF) TikTok Shop affiliate product imagery in bulk.

The goal of **v1** is one tight, reliable loop:

1. Read products from a local JSON file.
2. Build a BOF prompt for each product (retail-shelf placement, casual iPhone shopper aesthetic).
3. Log into Flow Labs once with a persistent browser profile.
4. For each product: upload the reference image, paste the prompt, click generate, wait, download the result, save it locally.

What this v1 explicitly does **not** do (yet):

- No video generation
- No ComfyUI / RunPod
- No Google Sheets, Discord review, n8n, or TikTok posting
- No model selection automation (you pick the Flow model once in the UI)

## Requirements

- Python 3.11+
- A Google account with access to Flow Labs
- Reference product images stored locally

## Install

```powershell
cd flow-bof-automation
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

## Configure

1. Drop reference product images into `inputs/sample_images/` (or anywhere — paths are absolute or repo-relative).
2. Edit `inputs/products.json` to match your products.
3. Edit `.env` to set `BROWSER_MODE`, timeouts, and Flow Labs URL.

## Browser modes

Google blocks sign-in inside Playwright's bundled Chromium, so the default mode is **`remote_debugging`**: you launch real Chrome yourself with a debug port, log into Flow Labs normally, and Playwright attaches to that existing session over CDP. No credentials are ever read or stored by this project.

`persistent_profile` (Playwright launches its own Chromium with a saved user-data dir) is kept as a fallback for environments where Google sign-in works.

Set the mode in `.env`:

```
BROWSER_MODE=remote_debugging
CHROME_CDP_URL=http://127.0.0.1:9222
FLOW_LABS_URL=https://labs.google/flow
```

### Windows remote debugging setup

1. **Close all Chrome windows.** Chrome only listens on the debug port if no other instance is using the same profile.

2. **Launch Chrome with the debug port** from PowerShell or cmd. This uses a dedicated profile so it won't disturb your normal Chrome:

   ```powershell
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\chrome-flow-automation"
   ```

   Keep this Chrome window open whenever you want to run automation. It is safe to close and reopen with the same command — your Google login will persist in that user-data-dir.

3. **Sign in to Google** in the new Chrome window and navigate to `https://labs.google/flow`.

4. **Verify Playwright can attach**:

   ```powershell
   python main.py --check-browser
   ```

   You should see the connected Chrome version, a list of open tabs, and a "Flow Labs reachable: yes" line.

5. **Run setup** (optional, just a guided version of the above):

   ```powershell
   python main.py --setup-browser
   ```

If `--check-browser` reports a connection failure, the most common cause is a Chrome instance already running with the default profile — close every Chrome window and re-launch with the command above.

## Run

### Daily flow

Approval is **favorite-based** — you heart the good images inside Flow Labs, and `--sync-favorites` flips matching CSV rows to `image_approved`. No manual status editing required.

```powershell
# 1. Drop your product photos into inputs/incoming_images/ and let the
#    scanner build the queue. Or use the manifest flow below.
python main.py --scan-images
python main.py --list-status

# 2. Generate up to N images. Each row clicks the arrow, then captures
#    the new tile's flow_media_id back onto the CSV row so favorites
#    can be matched later.
python main.py --generate-images --limit 30

# 3. In Flow Labs, visually heart the images you want to ship.

# 4. Sync favorites. Hearted rows → status=image_approved; everything
#    else stays at image_submitted. Prints approved id / product_name.
python main.py --sync-favorites
#    Equivalent explicit form:
python main.py --sync-favorites --approve-only

# 5. Animate the approved ones.
python main.py --generate-videos --limit 30
```

`--generate-videos` is the canonical alias for `--generate-videos-from-manifest`; it filters rows where `status=image_approved` AND `video_prompt` AND `flow_media_id` are all set.

If a row's `flow_media_id` was not captured during generate (network blip, capture timeout), run `python main.py --capture-tiles` once to bind the missed tiles back to their rows, then re-run `--sync-favorites`.

CSV columns: `id`, `product_name`, `image_path`, `image_prompt`, `video_prompt`, `status`, `category`, `store`, `placement_type`, `prompt_override`, `notes`. Status values: `pending`, `image_submitted`, `image_approved`, `image_rejected`, `video_pending`, `video_submitted`, `done`. The `id`, `image_prompt`, and `video_prompt` columns are written by the manifest workflow (see next section); the rest are written by `--scan-images`.

### Manifest flow (Markdown-driven, exact prompts)

When you want per-product BOF prompts and video prompts authored by hand, use a Markdown manifest instead of the scan flow.

1. Save reference images as `inputs/reference_images/01.jpg`, `02.jpg`, etc. Do not rename them.
2. Write `inputs/prompt_manifest.md`:

   ```markdown
   ## 01
   Product Name: Slim Fit Shapewear Bodysuit
   Reference Image: 01.jpg
   Status: pending

   Image Prompt:
   Use the reference image as the source of truth for the product…

   Video Prompt:
   Slow handheld dolly toward the product…

   ---

   ## 02
   …
   ```

   Each section is delimited by `## NN`. `Status` defaults to `pending`. `Image Prompt` and `Video Prompt` accept multi-line content; `---` separators between sections are optional.

3. Sync the manifest into `products.csv`:

   ```powershell
   # Merge into the existing CSV (preserves any in-progress rows from
   # earlier loads).
   python main.py --load-manifest

   # OR start a fresh batch — backs up the old CSV and starts from
   # scratch with only this manifest's products.
   python main.py --load-manifest --fresh
   ```

   Default (merge): new rows are added; existing rows (matched by `id`) get their `product_name`, `image_path`, `image_prompt`, `video_prompt` overwritten. `status` is only overwritten if the CSV row was still at `pending` — anything you've already advanced to `image_submitted` etc. is preserved.

   `--fresh`: existing `inputs/products.csv` is moved aside to `products.csv.bak.<timestamp>` and the manifest's products are written into a brand-new CSV. Use this when you start a new batch and don't want carry-over from the previous one.

4. Generate using the exact image prompts:

   ```powershell
   python main.py --generate-images-from-manifest --limit 10
   ```

   This only processes rows where `status=pending` AND `image_prompt` is set. Each row's `image_prompt` is sent to Flow Labs verbatim; no category lookup, no universal prompt.

### One-off / legacy commands (JSON workflow)

```powershell
# Print prompts only, no browser. Sanity-checks prompts + file paths
# against inputs/products.json.
python main.py --dry-run

# One-time browser setup (instructions + connection test).
python main.py --setup-browser

# Verify Playwright can attach to your manually-launched Chrome.
python main.py --check-browser

# Inspect the live Flow Labs page and dump candidate selectors + a screenshot.
python main.py --debug-selectors

# Record the Flow Labs UI flow once. See docs/record-flow-labs-actions.md.
python main.py --record-actions

# Run one full end-to-end generation against a JSON product.
python main.py --run-one --product-index 0

# Batch a few JSON products.
python main.py --limit 3
```

The per-step locators live in [src/recorded_flow.py](src/recorded_flow.py), the output of `playwright codegen` against your live Flow Labs session. When the UI changes, re-record — don't hand-edit selectors. Full walkthrough: [docs/record-flow-labs-actions.md](docs/record-flow-labs-actions.md).

## Output layout

```
outputs/
├── images/
│   └── 2026-05-26/
│       └── slim-fit-shapewear/
│           ├── slim-fit-shapewear_001.png
│           └── slim-fit-shapewear_001.prompt.txt
└── logs/
    └── 2026-05-26.log
```

The prompt used for each generation is saved next to the image so you can audit / re-run.

## Updating Flow Labs selectors

Flow Labs is a moving target. Every selector lives in [src/config.py](src/config.py#L1) under `FLOW_SELECTORS`. If automation breaks:

1. Run `python main.py --limit 1` and watch the browser.
2. When it fails, a screenshot is written to `outputs/logs/error_*.png`.
3. Open Flow Labs in DevTools, find the right selector, update `FLOW_SELECTORS` in [src/config.py](src/config.py).

Do **not** chase selectors inside `flow_automation.py` — they belong in one place.
