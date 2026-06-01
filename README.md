# flow-bof-automation

Automation for generating bottom-of-funnel (BOF) TikTok Shop affiliate images
and videos with [Google Flow Labs](https://labs.google/flow). Drives a real
Chrome window via the DevTools Protocol; runs the heavy lifting inside Docker.

> 📖 **In a hurry?** Open [README_FIRST.md](README_FIRST.md) — five steps,
> ten minutes. The rest of this file is the longer reference.

---

## What it does (one-paragraph version)

You paste in your favorited Kalodata products (or any product list). The app
fetches product photos, asks an AI (OpenAI / Anthropic / OpenRouter / or you
manually) to write a retail-shelf image prompt per product, then drives Flow
Labs to generate image variants for each one. You heart the ones you like
inside Flow. The app reads your hearts back, then animates each approved
image with a universal "slow handheld push-in + hand tap" video prompt.

```
Kalodata export ─► AI image prompts ─► Flow generates images ─► You ❤️ favorites
                                                                       │
                                                                       ▼
                                                          Flow generates videos
```

---

## Requirements

Everything is free except your AI API key.

| What                   | Why                              | Get it                                                              |
| ---------------------- | -------------------------------- | ------------------------------------------------------------------- |
| Windows 10/11 **or** macOS | Both supported (PowerShell on Windows, shell scripts on Mac). | (you already have one) |
| Docker Desktop         | Runs the UI + automation.        | <https://www.docker.com/products/docker-desktop/>                   |
| Google Chrome          | The browser Flow runs in.        | <https://www.google.com/chrome/>                                    |
| Google account         | To log into Flow Labs.           | <https://labs.google/flow>                                          |
| **One** of these AI keys (or skip with `manual`): | For prompt generation. | |
| &nbsp; • OpenAI        | `gpt-4o-mini` is cheap + great.  | <https://platform.openai.com/api-keys>                              |
| &nbsp; • Anthropic     | Best prompt quality.             | <https://console.anthropic.com/>                                    |
| &nbsp; • OpenRouter    | One key for many models.         | <https://openrouter.ai/keys>                                        |
| Optional: Kalodata     | Bulk-import favorited products.  | <https://www.kalodata.com/>                                         |

> ⚠️ A **ChatGPT subscription is not an OpenAI API key** — and a **Claude.ai
> subscription is not an Anthropic API key**. See [docs/API_KEYS.md](docs/API_KEYS.md).

You do **not** need to install Python, Playwright, or anything else on the
host. Everything runs in Docker.

---

## Install

1. **Install Docker Desktop** and start it. Wait for the whale icon to
   stop animating (system tray on Windows, menu bar on Mac).
2. **Install Google Chrome** if you don't have it.
3. **Unzip this folder** somewhere.

### Windows

```powershell
# Open PowerShell in the unzipped folder (Shift-right-click → "Open
# PowerShell window here") and run:
.\setup.ps1
```

### macOS

```bash
# Open Terminal in the unzipped folder and run:
chmod +x setup.sh start.sh stop.sh scripts/start_chrome_debug.sh
./setup.sh
```

`setup` builds the Docker images, creates the project folders, and writes
a default `.env`. Safe to re-run anytime. See [docs/MAC_SETUP.md](docs/MAC_SETUP.md)
for the Mac-specific walkthrough.

---

## Run

Windows:
```powershell
.\start.ps1
```

macOS:
```bash
./start.sh
```

This launches three things:

1. A **dedicated Chrome window** with remote debugging enabled (it's a
   throwaway Chrome profile under `%USERPROFILE%\chrome-flow-automation`, so
   your normal Chrome is untouched).
2. The Docker services (`cdp-proxy` + `ui`).
3. Your default browser, opened to <http://localhost:8080>.

> ⚠️ If Chrome was already running when you ran `start.ps1`, Windows will
> hand the launch off to the existing Chrome and silently ignore the debug
> flags. **Close all Chrome windows first**, then re-run `start.ps1`. The
> script warns you about this.

### First-time setup inside the UI

1. In the **Chrome window** that just opened, go to <https://labs.google/flow>
   and sign in with your Google account.
2. Back in the **Streamlit UI** at <http://localhost:8080>:
   - Open **Setup** in the sidebar.
   - Pick a provider (OpenAI / Anthropic / OpenRouter / Manual).
   - Paste your API key (it's masked everywhere; only stored in
     `data/secrets.local.json` on your machine).
   - Click **Test API key**.
   - Click **Save settings**.
3. Switch to **BOF Batch Builder** in the sidebar and follow the numbered
   steps top-to-bottom.

You never need to edit `.env` for API keys. Keys go through the UI.

---

## The workflow

The **BOF Batch Builder** page walks you through the full pipeline on one
screen. Each step is gated by the previous one — buttons show readiness
counts so you know when you can proceed.

| # | Step                  | What it does                                                          |
| - | --------------------- | --------------------------------------------------------------------- |
| 1 | Pick / create batch   | Each batch is a folder in `data/batches/`. Products + prompts live there. |
| 2 | Add products          | Drop a Kalodata `.xlsx`, paste a TikTok URL, or type manually.        |
| 3 | Reference images      | 1–3 product photos per card. Drag-drop or paste from clipboard.       |
| 4 | Generate AI prompts   | One click → AI fills `image_prompt` for every card that has photos.   |
| 5 | Export manifest       | Writes `inputs/prompt_manifest.md` (the file the Flow runner reads).  |
| 6 | Generate images       | Drives Flow Labs to produce image variants for every product.         |
| 7 | Heart + sync          | You ❤️ the keepers in Flow. The app reads them back, marks approved.    |
| 8 | Generate videos       | Animates every approved image with the **universal video prompt**.    |

### About video prompts

The app uses **one universal "blanket" video prompt for every product** to
prevent mismatches when you regenerate an image or manually heart an
alternate variant. You can edit that prompt inline in step 6 of the BOF
page; it persists to `data/settings.local.json`. Per-product video prompts
are still stored on each card but reserved for a future advanced mode.

Why this matters: if your product prompt says "slow pan over a coffee mug"
but you ended up favoriting a totally different image variant, the
per-product video prompt would generate weird artifacts. The universal
prompt is designed to work across all products: slow handheld push-in, one
hand tap, no morphing, no dramatic moves.

### About Kalodata import

The product table is uploaded via the `LIST_PRODUCT_FOCUS` sheet of a
Kalodata favorited-products export. Column names are matched loosely, so
small Kalodata schema changes don't break the import. Product images are
fetched from the URLs Kalodata embeds; if a fetch 403s you can drop the
image manually on each card.

---

## Daily operation

Windows:
```powershell
.\start.ps1   # in the morning, or whenever you want to work
.\stop.ps1    # when you're done (data + settings preserved)
.\reset.ps1   # clear runtime state, keep batches + settings
```

macOS:
```bash
./start.sh
./stop.sh
# (reset is Windows-only at the moment; remove the same files by hand if
# you need a runtime-state wipe on Mac:
#   rm -rf outputs/logs/* inputs/products.csv inputs/prompt_manifest.md \
#          data/unmatched_favorites.json
# )
```

| Script                   | What it does                                                          |
| ------------------------ | --------------------------------------------------------------------- |
| `setup.ps1` / `setup.sh` | Build images, create folders, bootstrap `.env`. Re-run any time.      |
| `start.ps1` / `start.sh` | Launch Chrome (debug) + Docker services + open the UI.                |
| `stop.ps1` / `stop.sh`   | `docker compose down`. Asks before closing Chrome. Preserves all data. |
| `reset.ps1` (Windows)    | Confirmed wipe of run state. **Keeps** batches, settings, API keys.   |

---

## CLI commands (advanced / debug)

The UI calls these under the hood. You can run them directly if you need
to debug. All commands run inside Docker.

```powershell
# Confirm the UI container can reach Chrome via the CDP proxy.
docker compose run --rm app python main.py --check-browser

# Validate the manifest is well-formed (parses, every section has a name +
# Reference Image + Image Prompt).
docker compose run --rm app python main.py --validate-manifest

# Merge inputs/prompt_manifest.md into inputs/products.csv. --fresh = wipe
# the CSV and start over (backs up the previous one).
docker compose run --rm app python main.py --load-manifest --fresh

# Generate images for up to N rows. Each row submits its image_prompt to
# Flow Labs and captures the new tile's flow_media_id back onto the row.
docker compose run --rm app python main.py --generate-images --limit 30

# Read your Flow Labs ❤️ favorites and flip matching rows to image_approved.
# Tile mapping is robust: works even if media_ids weren't captured at
# submit time (back-fills via tile_id).
docker compose run --rm app python main.py --sync-favorites

# Animate approved rows using the universal blanket video prompt.
docker compose run --rm app python main.py --generate-videos --limit 30
```

---

## File layout

```
flow-bof-automation/
├── README_FIRST.md             ← five-step quickstart for testers
├── README.md                   ← this file
├── setup.ps1 / start.ps1 / stop.ps1 / reset.ps1
├── docker-compose.yml
├── Dockerfile
├── main.py                     ← CLI entry point
├── streamlit_app.py            ← UI entry point
├── src/                        ← Python source
├── ai/                         ← OpenAI / Anthropic / OpenRouter providers
├── scripts/                    ← Chrome launcher, alpha packager
├── docs/
│   ├── QUICKSTART.md           ← first-batch walkthrough
│   ├── MAC_SETUP.md            ← macOS-specific install + commands
│   ├── API_KEYS.md             ← API keys + storage + rotation
│   ├── TROUBLESHOOTING.md      ← every alpha pitfall, indexed
│   ├── DISTRIBUTION.md         ← packaging the alpha ZIP
│   ├── DOCKER_SETUP.md         ← what the containers do
│   └── GITHUB_RELEASE_CHECKLIST.md ← pre-push hygiene
├── data/
│   ├── batches/<batch_id>/     ← your products + prompts
│   ├── settings.local.json     ← AI provider, model, blanket prompt
│   └── secrets.local.json      ← API keys (gitignored, never shipped)
├── inputs/
│   ├── reference_images/       ← photos you drag into the UI
│   ├── products.csv            ← run state (status, media IDs)
│   └── prompt_manifest.md      ← generated; the file the Flow runner reads
└── outputs/
    ├── images/                 ← (Flow keeps the originals; this is empty in practice)
    └── logs/                   ← one log file per CLI run
```

---

## Trouble?

Open the UI's **Setup** page and click **🩺 Run health check**. It tells you
which of these is wrong:

- Docker UI container
- Chrome remote debugging
- Flow Labs reachable
- AI provider configured
- Folders writable
- Batch exists

For the long form, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
The most common gotchas:

| Symptom                                       | Fix                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------- |
| `docker info` fails / hangs.                  | Start Docker Desktop. Wait for the whale icon to stop animating.    |
| UI says "Chrome debugger unreachable".        | Close **every** Chrome window. Re-run `start.ps1`.                  |
| Flow Labs opens but the run does nothing.     | You're not signed into Flow yet. Sign in inside the Chrome window.  |
| "OPENAI_API_KEY is empty" warning in Setup.   | Open Setup → paste key → Test → Save.                               |
| Every favorite is "unmatched".                | Click **Sync Favorites** again. The tile-id back-fill catches them. |
| Thumbnails in the unmatched section are broken. | Hard-reload (Ctrl-Shift-R). The inline-img fix is on current builds. |

---

## Privacy & security

- Your API keys live in `data/secrets.local.json` only. That file is
  `.gitignore`'d, `.dockerignore`'d, and excluded by `scripts/package_alpha.ps1`.
- The app never reads your normal Chrome profile — the debug Chrome runs
  out of `%USERPROFILE%\chrome-flow-automation` as a dedicated user-data dir.
- No telemetry. No phone-home. Logs stay in `outputs/logs/`.

---

## Packaging an alpha for another tester

```powershell
.\scripts\package_alpha.ps1
```

Produces `dist\flow-bof-automation-alpha-<YYYYMMDD>.zip`. The script
strips all secrets and run state, then errors out if it accidentally finds
either `secrets.local.json` or `.env` in the staged copy. See
[docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) for the full inclusion/exclusion list.

---

## Project state

- **Phase 5 (alpha distribution)** — current. UI-driven setup, lifecycle
  scripts, packaging, universal video prompt.
- Earlier phases: Docker (1), Streamlit UI (2), AI providers (3), one-page
  workflow + Kalodata import (4).
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).

## License

Private alpha. Do not redistribute the ZIP outside the tester group.
