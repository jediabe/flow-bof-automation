# GitHub release checklist

Pre-push hygiene. None of the items below remove or delete files — they're
the gates between a clean private repo and an accidental leak.

## Must NOT be committed

Environment + secrets:
- [ ] `.env` (your real env vars and maybe real keys)
- [ ] `.env.local`, `*.local.env`
- [ ] `data/secrets.local.json` — your API keys
- [ ] `data/settings.local.json` — model + provider choices, may include
      the OpenRouter site URL, etc.

User content:
- [ ] `data/batches/` — your product cards + prompts (private)
- [ ] `data/unmatched_favorites.json`
- [ ] `inputs/products.csv` (and `products.csv.bak.*`, `products.csv.*.tmp`)
- [ ] `inputs/reference_images/*` (your product photos, often copyrighted)
- [ ] `inputs/incoming_images/*`
- [ ] `inputs/prompt_manifest.md` (regenerated per run)
- [ ] Kalodata exports: `Kalodata_Product_*.xlsx`, anything else `*.xlsx` / `*.xls`
- [ ] Random `*.csv` you dropped in for testing

Runtime state:
- [ ] `outputs/logs/*` — one log per CLI run (may contain partial prompts)
- [ ] `outputs/images/*` — generated images
- [ ] `dist/` — packaged ZIPs

Local dev clutter:
- [ ] `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`
- [ ] `.vscode/`, `.idea/`
- [ ] `.browser_profile/`, `chrome-flow-automation/` (the Chrome user-data dirs)
- [ ] macOS: `.DS_Store`, `.AppleDouble`, `._*`
- [ ] Windows: `Thumbs.db`, `Desktop.ini`

## SHOULD be committed (the alpha needs these)

- [ ] `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- [ ] `.gitignore`
- [ ] `.env.example` and `.env.docker.example` (template values only)
- [ ] `README.md`, `README_FIRST.md`
- [ ] `requirements.txt`
- [ ] `main.py`, `streamlit_app.py`
- [ ] `setup.ps1` / `start.ps1` / `stop.ps1` / `reset.ps1` (Windows)
- [ ] `setup.sh` / `start.sh` / `stop.sh` (macOS)
- [ ] `src/`, `ai/`, `scripts/`, `docker/`, `docs/`
- [ ] `.gitkeep` placeholders in otherwise-empty shipped folders

## Pre-push commands

Run these in order before `git push`:

```bash
# 1. See exactly what would be pushed.
git status

# 2. Confirm no real keys are in the index. Adjust patterns as needed.
git grep -E 'sk-[A-Za-z0-9_-]{20,}'      || echo "OK: no OpenAI-style keys"
git grep -E 'sk-ant-[A-Za-z0-9_-]{20,}'  || echo "OK: no Anthropic-style keys"
git grep -E 'sk-or-v1-[A-Za-z0-9]{20,}'  || echo "OK: no OpenRouter-style keys"
git grep -E 'AIza[0-9A-Za-z_-]{30,}'     || echo "OK: no Google API keys"

# 3. Run a real secret scanner if you have one installed (gitleaks /
#    trufflehog / detect-secrets). Recommended for the first push.
gitleaks detect --no-banner            # if installed
```

## Procedure for the first push

1. Confirm `.gitignore` matches the "Must NOT be committed" list above.
2. `git status` — should show only the "SHOULD be committed" set.
3. If anything from the must-not list is staged, `git rm --cached <path>`
   and amend.
4. Run the secret-scan commands.
5. Push to a **private repo first**. Add testers as collaborators.
6. Only flip to public after at least one tester has verified the flow
   works end-to-end from the published archive.

## If a secret slips through

`git rm --cached` + amend only hides it from `HEAD`. Older commits still
contain the secret. To purge:

1. Rotate the leaked key on the provider's dashboard **first**.
2. `git filter-repo` (or BFG Repo-Cleaner) to rewrite history.
3. Force-push and notify any collaborators to re-clone.

> The checklist above prevents 99% of leaks. The rotation step is the only
> reliable mitigation after the fact.
