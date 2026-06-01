# macOS setup

Step-by-step for running flow-bof-automation on a Mac. The architecture is
identical to Windows — host Chrome in remote-debug mode + Docker for the UI
+ a CDP proxy between them — only the launch scripts differ.

## Prerequisites

| What                   | Why                              | Get it                                                              |
| ---------------------- | -------------------------------- | ------------------------------------------------------------------- |
| macOS                  | Apple Silicon or Intel.          | (you already have it)                                               |
| Docker Desktop for Mac | Runs the UI + automation.        | <https://www.docker.com/products/docker-desktop/>                   |
| Google Chrome          | The browser Flow runs in.        | <https://www.google.com/chrome/>                                    |
| Google account         | To log into Flow Labs.           | <https://labs.google/flow>                                          |
| Git (optional)         | Only if cloning the repo instead of using a ZIP. | <https://git-scm.com/download/mac>                |

You do **not** need Python or Playwright on the host. Everything runs
inside Docker.

## First-time setup

Open Terminal, `cd` into the unzipped folder, then:

```bash
chmod +x setup.sh start.sh stop.sh scripts/start_chrome_debug.sh
./setup.sh
```

`setup.sh` verifies Docker, creates the project folders, copies
`.env.example` → `.env`, sets the executable bit on the rest of the shell
scripts, and runs `docker compose build`. Safe to re-run.

## Daily startup

```bash
./start.sh
```

This launches:
1. A dedicated Chrome window with remote debugging on port 9222.
   - Profile dir: `~/chrome-flow-automation` (separate from your normal Chrome).
2. The Docker services (`cdp-proxy` + `ui`).
3. Your default browser, opened to <http://localhost:8080>.

### In the Chrome window

1. Go to <https://labs.google/flow> and log in with your Google account.
2. **Keep this Chrome open** for the entire session — closing it kills the
   debug port, and the UI will lose contact with Flow.

### In the UI

1. Open **Setup** in the sidebar.
2. Pick provider, paste API key, **Test API key**, **Save settings**.
3. Switch to **BOF Batch Builder** and work through the numbered steps.

## Verification

To confirm Docker can reach Chrome:

```bash
docker compose run --rm app python main.py --check-browser
```

Expected output ends with a "Flow Labs reachable: yes" line and a list of
open tabs in the debug Chrome.

## Normal workflow

Same as Windows, all driven from **BOF Batch Builder** in the UI:

1. Upload a Kalodata `.xlsx` export (or paste a TikTok URL).
2. Drop reference images for each product.
3. Click **Generate AI prompts** — fills `image_prompt` for every card.
4. Click **Prepare image batch** (exports the manifest).
5. Click **Generate Images** — drives Flow Labs to produce variants.
6. Heart your favorites in the Chrome window.
7. Click **Sync Favorites** in the UI — flips matching rows to approved.
8. Click **Generate Videos** — animates approved images using the
   universal blanket prompt (editable inline; same for every product).

## Stopping

```bash
./stop.sh
```

Brings the Docker services down and offers to quit Chrome. Your batches,
settings, and API keys are preserved. Run `./start.sh` to resume.

## Troubleshooting

| Symptom                                       | Fix                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------- |
| `docker info` errors / hangs.                 | Open **Docker Desktop**. Wait for the whale icon in the menu bar to stop animating. |
| `./setup.sh: Permission denied`               | Run `chmod +x setup.sh start.sh stop.sh scripts/start_chrome_debug.sh` and retry. |
| `Google Chrome not found at /Applications/Google Chrome.app` | Install Chrome from <https://www.google.com/chrome/>. If you installed it somewhere else, move it to `/Applications/`. |
| "Chrome debugger unreachable" in the UI Setup health check. | Chrome was already running when `start.sh` fired. Quit Chrome entirely (Cmd+Q in the menu bar; check the dot under the icon is gone), then `./start.sh` again. |
| CDP proxy connection failed (`502` in `docker compose logs cdp-proxy`). | The proxy forwards `cdp-proxy:9333 → host.docker.internal:9222`. If Chrome is down on the host, every request 502s. Run `./start.sh` to relaunch Chrome. |
| Flow Labs opens but the run does nothing.     | You're not signed into Flow yet. In the Chrome debug window, open <https://labs.google/flow> and sign in. |
| Setup says "OPENAI_API_KEY is empty".         | Open **Setup** → paste key → **Test API key** → **Save settings**. Keys go in the UI; never edit `.env`. |
| macOS warns about "scripts from the internet" when you double-click `start.sh`. | Run from Terminal instead of double-clicking. The `chmod +x` step grants execute permission; Gatekeeper still nags on Finder launches. |
| Apple Silicon: image build is slow / "no matching manifest". | Docker Desktop for Mac runs both arm64 and amd64 images. The Dockerfile uses base images that publish both architectures. If you see a manifest error, run `docker compose build --no-cache`. |

For the deeper catalog, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — most
of the symptoms are platform-agnostic.

## What the scripts actually do

- `setup.sh` — Docker check, folder creation, `.env` bootstrap, `chmod +x`
  on other scripts, `docker compose build`.
- `start.sh` — runs `scripts/start_chrome_debug.sh`, then
  `docker compose up -d cdp-proxy ui`, waits for the UI, opens the browser.
- `stop.sh` — `docker compose down --remove-orphans`, prompts to quit
  Chrome.
- `scripts/start_chrome_debug.sh` — execs
  `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` with
  `--remote-debugging-port=9222 --remote-debugging-address=0.0.0.0
  --remote-allow-origins=* --user-data-dir="$HOME/chrome-flow-automation"`.

These mirror the four `*.ps1` scripts at the project root used on Windows.
The PowerShell scripts and shell scripts share no logic; they each launch
the same Docker services and the same Chrome flags, just through their
host's native interpreter.
