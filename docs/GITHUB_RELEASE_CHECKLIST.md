# GitHub Release Checklist

Before pushing this repo to GitHub, confirm the following:

- [ ] `.env` is not committed and is ignored by `.gitignore`
- [ ] `.env.local` and `*.local.env` are ignored
- [ ] `data/secrets.local.json` is not committed
- [ ] `data/settings.local.json` is not committed if it contains keys or private settings
- [ ] Kalodata export files such as `Kalodata_Product_*.xlsx` are not committed
- [ ] generated outputs and logs under `outputs/` are not committed
- [ ] `outputs/logs/` is not committed
- [ ] `inputs/products.csv` is not committed
- [ ] `inputs/reference_images/` is not committed
- [ ] `data/batches/` is not committed
- [ ] `data/unmatched_favorites.json` is not committed
- [ ] `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `dist/`, `.DS_Store`, `.vscode/`, `.idea/`, and `chrome-flow-automation/` are ignored
- [ ] no real API keys are present in committed files
- [ ] run `git status` before `git push`
- [ ] run a secret scan before push
- [ ] push to a private repo first for alpha testing

> Note: This checklist is for repository hygiene only. It does not remove or delete any files.
