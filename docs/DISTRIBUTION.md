# Distribution — packaging the alpha for a tester

The goal is a single ZIP a tester can unzip and run with `setup.ps1` /
`start.ps1`. They should never need to edit `.env`, never see your keys,
never need git.

## The one-command path

```powershell
.\scripts\package_alpha.ps1
```

Output: `dist\flow-bof-automation-alpha-<YYYYMMDD>.zip`.

That's it. Hand the ZIP off.

## What gets included

Source + config — everything the tester needs to build and run:

```
Dockerfile
docker-compose.yml
.dockerignore
.gitignore
.env.example                ← template, no real keys
README_FIRST.md
README.md
requirements.txt
main.py
streamlit_app.py
setup.ps1 / start.ps1 / stop.ps1 / reset.ps1  ← Windows
setup.sh  / start.sh  / stop.sh               ← macOS
src/      ← all Python source
ai/       ← provider plugins
scripts/  ← chrome launcher, package_alpha.ps1 itself, etc.
docker/   ← cdp-proxy nginx config
docs/     ← all of the .md files
```

Empty skeleton directories (with `.gitkeep` so they survive unzipping):

```
inputs/                  inputs/reference_images/   inputs/incoming_images/
outputs/                 outputs/images/            outputs/logs/
data/                    data/batches/
```

## What is EXCLUDED — and why

| Excluded                              | Reason                                          |
| ------------------------------------- | ----------------------------------------------- |
| `.git/`                               | Git history isn't useful to a tester.           |
| `.venv/`                              | Host Python virtualenv; container has its own.  |
| `__pycache__/`, `.pytest_cache/`      | Build artifacts.                                |
| `.env`                                | Your real env vars + maybe your real keys.      |
| `data/secrets.local.json`             | **Your API keys.** Critical to exclude.         |
| `data/settings.local.json`            | Your model/provider choices.                    |
| `data/unmatched_favorites.json`       | Runtime state from your sessions.               |
| `data/batches/*`                      | Your products + prompts (private).              |
| `outputs/logs/*`, `outputs/images/*`  | Run logs, generated outputs.                    |
| `inputs/products.csv` + backups       | Your run state.                                 |
| `inputs/prompt_manifest.md`           | Generated per-run; tester regenerates theirs.   |
| `inputs/reference_images/*`           | Your product photos (often copyrighted).        |
| `inputs/incoming_images/*`            | Same.                                           |
| `node_modules/`                       | Not used; safety net.                           |
| `dist/`                               | Avoid recursive packaging of old ZIPs.          |

`package_alpha.ps1` has an explicit **paranoia step** that scans the staging
tree for `secrets.local.json` and `.env` and **errors out** rather than
shipping if either is found. If it fails, fix it; don't bypass the check.

## What the tester does

1. Unzip into a folder.
2. Open PowerShell in that folder.
3. `.\setup.ps1` → builds Docker images, creates folders, writes `.env` from
   `.env.example`.
4. `.\start.ps1` → launches Chrome (debug) + Docker services + opens UI.
5. In the UI, **Setup** → enter API key → Test → Save.

No `.env` editing. No long Docker commands. The tester's `.env` is whatever
`.env.example` ships with — entirely template values, since real config goes
through the UI.

## Versioning

Embed the date in the ZIP name (`alpha-20260530.zip`). For internal alphas
that's enough; tag in git separately if you want a permanent record.

## Hot-fixing a tester's install

If a tester needs a code-only patch (no Docker rebuild):
1. Send them the changed `.py` files.
2. They overwrite in their unzipped folder.
3. `docker compose restart ui`.

If the patch touches `requirements.txt` or the Dockerfile, send a fresh ZIP
and have them re-run `setup.ps1`.

## Things that are NOT in scope for the alpha

- Updating in place (no auto-updater; testers re-unzip).
- Multi-tester telemetry.
- Anything that touches the host's main Chrome profile (the debug profile
  is its own `chrome-flow-automation` folder under `%USERPROFILE%`).
