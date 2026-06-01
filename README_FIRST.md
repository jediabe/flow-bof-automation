# Flow BOF Automation — Quick Start (private alpha)

Five steps. Most testers are running their first batch inside 10 minutes.

## 1. Install Docker Desktop

Download from <https://www.docker.com/products/docker-desktop/> and start it.
Wait until the whale icon in the Windows tray is steady (not animated).

## 2. Run setup

Open PowerShell in this folder (the one containing this README) and run:

```powershell
.\setup.ps1
```

It builds the Docker images, creates the project folders, and bootstraps a
`.env` file. Safe to re-run.

## 3. Start the app

```powershell
.\start.ps1
```

This launches:
- A dedicated Chrome window with remote-debugging enabled.
- The `cdp-proxy` and `ui` Docker containers.
- Your browser, pointed at <http://localhost:8080>.

## 4. Log into Flow

In the **Chrome window that just opened**, go to <https://labs.google/flow>
and log in with your Google account. The UI container only needs you to be
logged in once — it controls the same Chrome via the debug port.

## 5. Configure your AI key (in the UI)

In the Streamlit UI:
1. Open **Setup** in the sidebar.
2. Pick your provider (OpenAI / Anthropic / OpenRouter / Manual).
3. Paste your API key (it's saved locally, masked everywhere it's shown).
4. Click **Test API key** to verify.
5. Click **Save settings**.

You don't need to edit `.env`. Keys go in the UI; they're written to
`data/secrets.local.json` and never committed or copied into Docker images.

## What's next?

Open **BOF Batch Builder** in the sidebar and follow the steps top to bottom.
For more detail see [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Stopping

```powershell
.\stop.ps1
```

Your data, settings, and API keys are preserved. Run `.\start.ps1` to resume.

## Trouble?

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — it covers every
symptom we hit during the alpha.
