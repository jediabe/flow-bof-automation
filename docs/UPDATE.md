# Updating to the latest version

You shouldn't have to know what `git pull` is to keep this app current.
The update script handles the entire process — back up your data, save
any accidental local edits, download the latest version, rebuild Docker,
restart the UI.

## Simple update

### Windows

```powershell
.\update.ps1
```

### macOS

```bash
chmod +x update.sh
./update.sh
```

That's it. The script does the rest.

## What the updater does

1. Confirms the folder is a real git repo.
2. Creates `backups/update_<timestamp>/`.
3. **Backs up your local settings and batches**:
   - `.env`
   - `data/settings.local.json` (provider, model, blanket prompt)
   - `data/secrets.local.json` (your API keys)
   - `data/batches/` (your product cards + prompts)
   - `data/unmatched_favorites.json`
   - `data/video_submitted_tiles.json`
   - `inputs/products.csv`
   - `inputs/reference_images/`
   - `inputs/incoming_images/`
   - `outputs/logs/`
4. **Saves accidental local code edits** as `local_changes.patch` +
   `staged_changes.patch` inside the backup folder.
5. **Stops the Docker containers** so the rebuild step has a clean slate.
6. **Downloads the latest version** with `git fetch origin` +
   `git reset --hard origin/main`. This intentionally **discards local
   edits** to the source files — they were saved as patches in step 4.
7. **Rebuilds Docker images** so any dependency / Dockerfile changes
   take effect.
8. **Restarts the app** via `docker compose up -d --force-recreate cdp-proxy ui`.

When it's done, open <http://localhost:8080>.

## ⚠️ If you intentionally edited the source code

The updater **resets every tracked file** to the GitHub version. Your
edits are preserved as a unified-diff patch in the backup folder, but
they aren't applied to the new code — that would risk a merge conflict
the updater is explicitly designed to avoid.

To re-apply your changes after an update:

```bash
git apply backups/update_<timestamp>/local_changes.patch
```

If `git apply` reports conflicts, the upstream code changed the same
lines you did; you'll need to resolve by hand.

For most testers this section doesn't apply — you can ignore it.

## Where backups are stored

```
backups/
└── update_20260601_193245/
    ├── .env                              ← your saved env
    ├── data/                             ← user data tree
    │   ├── batches/
    │   ├── secrets.local.json
    │   ├── settings.local.json
    │   ├── unmatched_favorites.json
    │   └── video_submitted_tiles.json
    ├── inputs/
    │   ├── products.csv
    │   ├── reference_images/
    │   └── incoming_images/
    ├── outputs/
    │   └── logs/
    ├── local_changes.patch               ← from `git diff`
    ├── staged_changes.patch              ← from `git diff --staged`
    └── git_status_before.txt
```

Backups are never deleted automatically. Once you're confident an update
worked, you can remove old `backups/update_*/` folders by hand to free
space.

## Manual fallback

If `update.ps1` / `update.sh` fails partway, you can run these commands
by hand (this is exactly what the script would have done):

```bash
git fetch origin
git reset --hard origin/main
docker compose build
docker compose up -d --force-recreate cdp-proxy ui
```

User data (anything under `data/`, `inputs/`, `outputs/`) is unaffected
by `git reset --hard` because it's gitignored.

## Troubleshooting

### "This folder is not connected to GitHub"

`update.ps1`/`update.sh` only works on a git clone. If you unzipped a
release ZIP instead, you have two options:

1. Re-download the latest ZIP from GitHub and unzip over the same
   folder, **preserving your `.env`, `data/`, and `inputs/` directories.**
2. Convert the unzipped folder into a clone:
   ```bash
   git init
   git remote add origin <https URL of the repo>
   git fetch origin
   git reset --hard origin/main
   ```

### "Docker Desktop is not running" warning during update

The script updates the source code anyway and tells you what to run
once Docker is up:

```bash
docker compose build
docker compose up -d --force-recreate cdp-proxy ui
```

### "Permission denied: ./update.sh" (macOS)

```bash
chmod +x update.sh
./update.sh
```

The Mac `setup.sh` runs this `chmod` for you on first install. If you
copied the file from elsewhere it may have lost its executable bit.

### "fatal: not a git repository (or any of the parent directories)"

Same as the first item — you're not in a git clone. Either re-download
or run the `git init` recipe above.

### "git fetch origin failed (no internet? wrong remote?)"

- Check your connection.
- Confirm the remote is set:
  ```bash
  git remote -v
  ```
  It should show two `origin` lines pointing at the repo's HTTPS URL.

### "docker compose build failed"

Usually a transient base-image throttle or a credential-helper PATH
glitch (Mac). Re-run `update.ps1`/`update.sh` — the backup is already
in place, so it's safe to retry.

### Local changes were reset but saved in backup patch

Working as intended. Your edits are at
`backups/update_<timestamp>/local_changes.patch`. To re-apply:

```bash
git apply backups/update_<timestamp>/local_changes.patch
```

If you only wanted to inspect your edits (not re-apply them), just open
the `.patch` file in any editor — it's a regular unified diff.
