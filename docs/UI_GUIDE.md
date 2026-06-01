# UI guide (Phase 3 — guided workflow)

A local Streamlit UI that wraps the existing CLI. Same automation logic, friendlier surface. The sidebar walks you through the daily flow in order; **Advanced / legacy** holds every original tool for debugging.

## Sidebar layout

```
Flow BOF Automation
─────────────────────────
1. Dashboard          ← overview + "next action" hint
2. Product Intake     ← bulk add (paste JSON) + manual add
3. Images             ← image pool, clipboard, per-product status
4. AI Prompts         ← batch generation + per-product edit
5. Export Manifest    ← readiness check + sync to inputs/
6. Flow Batch Run     ← Prepare+Generate, Sync Favorites, Generate Videos
7. Logs               ← today's log + last command + error screenshots

▾ Advanced / legacy
   AI Product Intake (legacy)   ← the original single-page version
   Reference Images (legacy)    ← raw upload/clear-all
   Manifest Builder (legacy)    ← raw markdown editor
   Batch Controls (legacy)      ← raw per-command buttons
   Original Dashboard           ← unchanged old dashboard
   Settings                     ← env / paths / per-session defaults
```

The **Dashboard → "Next action"** card has a one-click *Go to* button that jumps to whichever page handles the next step. Every guided page also shows that same card at the top so you can always see where you are in the flow.

## Fast daily workflow

```text
1. Open the UI.
2. Sidebar → 1. Dashboard → Create / select batch.
3. Sidebar → 2. Product Intake → paste a JSON array of TikTok URLs → Create Product Cards.
4. Sidebar → 3. Images → upload to the Image Pool, attach to cards
              (or use the Clipboard Image Intake to paste from your browser).
5. Sidebar → 4. AI Prompts → pick provider/model → click
              "Generate AI Prompts for All Ready Products".
6. Sidebar → 5. Export Manifest → review readiness → Export + Sync Manifest.
7. Sidebar → 6. Flow Batch Run → "Prepare + Generate Images (limit N)".
8. In your real Chrome Flow window, heart the images you want to ship.
9. Back to 6. Flow Batch Run → Sync Favorites.
10. 6. Flow Batch Run → Generate Videos for Approved Products.
```

Every long-running step streams its output into the page; nothing happens until you click.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Windows host                                                    │
│                                                                  │
│   Real Chrome  --remote-debugging-port=9222                      │
│        ▲                                                         │
│        │   (Host: 127.0.0.1:9222 — see cdp-proxy.conf)           │
│   ┌────┴───────────────────────────────────┐                     │
│   │  [cdp-proxy]   nginx, port 9333        │                     │
│   └────────────────────────────────────────┘                     │
│        ▲                            ▲                            │
│        │ http://cdp-proxy:9333      │                            │
│        │                            │                            │
│   ┌────┴────────────┐         ┌─────┴───────────────┐            │
│   │ [app] CLI       │         │ [ui]  Streamlit     │            │
│   │  python main.py │         │  streamlit_app.py   │            │
│   │                 │         │  subprocesses       │            │
│   │                 │         │  python main.py …   │            │
│   └─────────────────┘         └──────────┬──────────┘            │
│                                          │ port 8080             │
└──────────────────────────────────────────┼──────────────────────-┘
                                           │
                                           ▼
                              http://localhost:8080 (your browser)
```

The UI is just a presentation layer. When you click *Generate Images* it spawns the same `python main.py --generate-images …` that you'd run from PowerShell. **No business logic is duplicated.**

## Running it

Once Chrome and Docker are running:

```powershell
scripts\start_ui.ps1
```

That:
1. Warns if host Chrome on port 9222 isn't responding.
2. Runs `docker compose up -d ui` (which also brings up `cdp-proxy` via `depends_on`).
3. Waits for Streamlit's HTTP listener to come up.
4. Opens `http://localhost:8080` in your default browser.

To stop only the UI:

```powershell
docker compose stop ui
```

To stop everything (UI + cdp-proxy):

```powershell
scripts\stop_app.ps1
```

The existing CLI keeps working in parallel — `docker compose run --rm app python main.py …` is unchanged.

## Pages (guided workflow)

### 1. Dashboard

- **Next action card** — the recommender looks at your batch state + the runtime CSV state and picks one of: create batch, add products, add images, generate prompts, export manifest, generate images, sync favorites, generate videos, or "batch (mostly) done". Click the *Go to …* button to jump to the right page.
- **Connectivity panel** — pings `http://cdp-proxy:9333/json/version` so you can see at a glance whether the container can reach host Chrome.
- **Batch counts** — Products, With images, With prompts, Ready to export; plus CSV-side counts (rows, image_submitted, image_approved, video_submitted).

### 2. Product Intake

- **Batch selector + New batch button.**
- **Import from Kalodata Excel (preferred)** — upload a Kalodata `.xlsx` export. Reads the `LIST_PRODUCT` sheet, previews the rows (name, category, price, commission, revenue, growth, creators, TikTokUrl, whether `img_url` is present), and creates one product card per row on click. Each card gets:
  - `product_name`, `original_title` ← Product Name
  - `tiktok_url` ← TikTokUrl
  - `product_description` ← `<Product Name> [<Category>]`
  - `notes` ← `Price | Commission Rate | Revenue | Revenue Growth | Creator Number | Kalodata URL`
  - `category` ← Category
  - `reference_images[0]` ← image downloaded from `img_url` directly into `data/batches/<batch_id>/reference_images/<product_id>_primary.<ext>`
  - If the download fails (CDN block, network blip), the card is still created with a warning so you can paste/attach the image manually.
  - Scope: *All rows* or *First N rows*.
- **Bulk add via JSON** — paste a JSON array of `{title, url}` objects, click *Parse Products* to preview, then *Create Product Cards* to add them all. Trailing `- TikTok Shop` and excessive leading hashtags are stripped; `original_title` is preserved on each card.
- **Add one product manually** — collapsed expander; creates a single blank card you fill in on the AI Prompts page.
- **Products list (compact)** — one row per product with thumbnail + URL + status chips + an *Edit* button that opens the full card on the AI Prompts page.

### 3. Images

- **Image pool** (preferred) — bulk-upload images into `data/batches/<batch_id>/image_pool/`. Each pool image has a *Attach to product* + *Role* picker + *Attach* button. Filenames don't have to match anything. Overwriting a filled slot requires a second click.
- **Clipboard Image Intake** — click the paste button, choose target product, *Attach*. Stages PNG in the next free reference slot for that product.
- **Per-product image status + filter** — compact rows with `url / img / ip / vp / ready` chips. Filter dropdown: All / Missing images / Has images.
- *Edit* button on any row opens the full per-card uploader + paste button below the list, so you can add images without leaving the page.

### 4. AI Prompts

- **AI provider panel** — pick `openai` / `anthropic` / `openrouter` / `manual` and the model. Green chip = configured; red chip = API key missing. For `openrouter` with no model set, a yellow note announces auto-routing.
- **Batch generation** — *Generate AI Prompts for All Ready Products*. Two toggles:
  - *Overwrite existing prompts* (default off) — leave OFF to skip products that already have image_prompt + video_prompt.
  - *Include products without images* (default off) — Flow generation needs an image, so off by default.
  The runner shows a live progress bar, a status line ("[i/N] product name…"), and a rolling log of `OK / WARN / FAIL` per product. One failure doesn't stop the batch.
- **Manual provider blocks the batch run** — you'll see "Manual provider selected. Enter prompts manually per card below."
- **Per-product compact list + Edit** — same row layout as Images. Click *Edit* to open the full card editor below: name / URL / description / notes / paste button / image_prompt / video_prompt / hook / caption / category / store_environment / placement_type / Save / Delete.

### 5. Export Manifest

- **Readiness table** — one row per product with booleans for `has_image`, `has_img_pr`, `has_vid_pr`, `ready`, plus a `skip_reason` column listing exactly what's missing.
- **Export + Sync Manifest** — writes `data/batches/<batch_id>/prompt_manifest.md`; with the *Sync to inputs/* checkbox on (default) also copies it + reference images into `inputs/prompt_manifest.md` / `inputs/reference_images/` so the existing CLI flow picks them up.
- **Include incomplete products** — surfaces a clear note that the manifest format requires the three core fields; non-ready products will still be skipped. (The toggle is informational for now.)

### 6. Flow Batch Run

Three stacked sections corresponding to steps 6, 7, 8 of the daily flow:

- **Prepare + Generate Images** — one button that subprocesses, in order, `--validate-manifest`, `--load-manifest --fresh`, `--generate-images --limit N`. Stops at the first non-zero exit. Requires a confirmation checkbox because `--fresh` rewrites the CSV.
- **Run sub-steps individually** — collapsed expander with raw buttons for *Check Browser*, *Validate Manifest*, *Load Manifest Fresh*, *Generate Images*. Useful for debugging.
- **Sync Favorites** — single button; runs `--sync-favorites`. Shows current submitted/approved/video_submitted counts.
- **Generate Videos for Approved Products** — runs `--generate-videos --limit N`.

### 7. Logs

Unchanged from earlier phases: today's log file (`outputs/logs/YYYY-MM-DD.log`), last UI-triggered command's output, recent error screenshots, list of all log files.

## Advanced / legacy

Collapsed expander in the sidebar exposes these original pages for debugging:

- **AI Product Intake (legacy)** — the original single-page "everything" view. Useful when you want all controls in one scroll.
- **Reference Images (legacy)** — the original raw uploader for `inputs/reference_images/` outside the batch workflow.
- **Manifest Builder (legacy)** — raw Markdown editor for `inputs/prompt_manifest.md`.
- **Batch Controls (legacy)** — the original per-button page (`Check Browser`, `Validate Manifest`, `Load Manifest Fresh`, `Generate Images`, `Sync Favorites`, `Generate Videos`).
- **Original Dashboard** — the older, less-opinionated dashboard.
- **Settings** — env / paths / per-session UI defaults.

All Advanced pages remain functional and pass through to the same underlying CLI commands. Nothing was removed.

## Original AI Product Intake (Phase 3 + UX upgrade)

The fastest path from a scrape/list of TikTok URLs to a Flow-ready manifest. The page has six stacked sections, listed top to bottom:

1. **Batch selector** — pick an existing `data/batches/<batch_id>/` or create a new one with **New batch**.
2. **AI provider** — pick `openai` / `anthropic` / `openrouter` / `manual`. The matching `*_MODEL` env var shows as an editable field. A green chip means the API key is configured; red means missing — see [AI_PROVIDERS.md](AI_PROVIDERS.md). For `openrouter` without `OPENROUTER_MODEL` set, a yellow warning announces auto-routing.
3. **Bulk add products (paste JSON)** — paste an array of `{title, url}` objects (Kalodata exports, TikTok scrapes). Parse → preview table (cleaned name, URL, detected source) → **Create product cards**. The unmodified title is preserved in each card's `original_title`. Trailing `- TikTok Shop` and excessive leading hashtags are stripped; the rest is left alone.
4. **Image pool (bulk upload + attach)** — drop many images at once into `data/batches/<batch_id>/image_pool/`. Filenames don't have to match product names. Each pool image renders with a thumbnail + product selector + role selector + Attach button. Overwriting an already-filled slot requires a second click. A "pool maintenance" toggle lets you remove pool images.
5. **Clipboard image intake** — global paste landing area. Click the paste button (browser may prompt for clipboard permission), choose which product the staged image belongs to, click Attach. Pasted images go to PNG in the next free reference slot.
6. **Products in batch** — the main editor. Each card shows status chips (`url`, `img`, `ip`, `vp`, `ready`). Filter dropdown narrows the view (All / Missing images / Missing prompts / Ready to export). Per-card inputs: name, TikTok URL, description, notes; reference-image uploader; per-card paste button; **Generate AI prompts** button; editable image/video/hook/caption/category/store/placement fields; Save / Delete.
7. **Export / Sync Manifest** — writes `data/batches/<batch_id>/prompt_manifest.md`; with the sync checkbox (default on) also copies it and every used reference image to `inputs/prompt_manifest.md` and `inputs/reference_images/` so the existing CLI flow keeps working. Products missing any of {product_name, reference image, image_prompt, video_prompt} are SKIPPED — the page tells you the ready vs. skipped counts upfront.

#### Daily flow with the UX upgrade

1. Paste a JSON array of new product URLs in **Bulk add** → Parse → Create cards.
2. Drop a bunch of product photos into **Image pool**.
3. For each card, either:
   - Use the **Image pool** section to assign pool images to roles, or
   - Open the card and use **📋 Paste image** to drop a screenshot from your clipboard.
4. For each card, click **Generate AI prompts** (or fill prompts manually if `AI_PROVIDER=manual`).
5. Click **Save product** on each.
6. Filter to "Ready to export" to confirm everything's green.
7. **Export manifest** (sync to inputs left on).
8. Switch to **Batch Controls** → **Load Manifest** (Fresh) → **Generate Images** → heart in Flow → **Sync Favorites** → **Generate Videos**.

#### Notes on clipboard paste

Real Ctrl+V on a div would require a bi-directional Streamlit component; we use the simpler [`streamlit-paste-button`](https://pypi.org/project/streamlit-paste-button/) which is a button that triggers the browser's clipboard API. You still get one-click paste; you just click the button instead of pressing Ctrl+V on a paste zone. Both per-card and global paste buttons work the same way.

If you see "the `streamlit-paste-button` package isn't installed" warnings, rebuild the image:

```powershell
docker compose build
docker compose restart ui
```

### Dashboard

### Dashboard

- Live check of `http://cdp-proxy:9333/json/version` — green if the container can reach host Chrome, red with the underlying error if not.
- Status histogram of `inputs/products.csv` (pending / image_submitted / image_approved / image_rejected / video_pending / video_submitted / done).
- "Next action" hint based on the histogram: tells you which step is up next.

### Reference Images

- Multi-file upload widget writes directly into `inputs/reference_images/` (bind-mounted from the host).
- Thumbnail grid of every supported image (.jpg, .jpeg, .png, .webp).
- Per-tile *Delete* button.
- A guarded *Clear all* with an explicit confirmation checkbox + Type-to-confirm style.

### Manifest Builder

- Text-area editor for `inputs/prompt_manifest.md`. *Save* writes back; *Re-load from disk* discards in-editor changes.
- *Validate (CLI)* subprocesses `python main.py --validate-manifest …` and streams the same output you'd see from the shell.
- Parsed-products table: id, product_name, reference_image, resolved path, file-exists, prompt sizes, status, missing fields. Mirrors `--validate-manifest` but in a friendlier table form.

### Batch Controls

The action buttons. Each one shells out to `python main.py …`:

| Button | Command | Notes |
| --- | --- | --- |
| Check Browser | `--check-browser` | confirms Playwright can attach via cdp-proxy |
| List Status | `--list-status` | quick status histogram |
| Validate Manifest | `--validate-manifest <path>` | read-only |
| Load Manifest [Fresh] | `--load-manifest <path> [--fresh]` | `--fresh` is gated by a checkbox + warning |
| Generate Images (limit N) | `--generate-images --limit N` | long-running |
| Sync Favorites | `--sync-favorites` | matches favorited tiles to CSV rows |
| Generate Videos (limit N) | `--generate-videos --limit N` | long-running |

The limit is a numeric input shared across the page.

### Logs

- Today's log file (`outputs/logs/YYYY-MM-DD.log`), trimmed to the last 20 000 chars.
- Last command output captured during this UI session — useful when scrolling back.
- The eight most recent `error_*.png` screenshots (any crash takes one automatically).
- A list of all `*.log` files with sizes.

### Settings

- Per-session default for the generate limit.
- Read-only dump of the container's relevant environment variables.
- Project path table with existence flags.

## Caveats

- **The UI is single-threaded.** While a *Generate Images* or *Sync Favorites* subprocess is running, the page can't be navigated. Output streams into a live code block as it arrives. If you must do something else, open another browser tab and run the CLI directly via `docker compose run --rm app …`.
- **No auto-run.** Loading a page never triggers a destructive operation. Every state-changing action requires an explicit button click.
- **`--fresh` is gated.** The Load Manifest button only adds `--fresh` when you tick the corresponding warning checkbox.
- **Errors are surfaced.** Subprocess exit code, full stdout+stderr, and the last command record are all visible — no swallowed errors.

## When to use the UI vs the CLI

- Use the UI when you want at-a-glance status, easy manifest editing, or simple uploads.
- Use the CLI for scripting, scheduled jobs, or when an SSH session is all you have.

Both share the same code path, so either is safe to switch to mid-batch.

## Rebuilding after `requirements.txt` changes

The UI service uses the same image as the app service (`flow-bof-automation:latest`). When you change `requirements.txt`:

```powershell
docker compose build
docker compose up -d ui      # picks up the rebuilt image
```

Code edits in `streamlit_app.py` / `src/` don't need a rebuild — Streamlit auto-reloads when files in `/app` change (the project root is bind-mounted).
