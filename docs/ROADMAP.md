# Roadmap

Tracking ideas that aren't built yet. Nothing here is a commitment.

## Near-term (next 1-2 phases)

- **Auto-fetch product images from a TikTok/Kalodata URL.** Given a product URL, scrape the OpenGraph image + the first 1-3 gallery photos. Drop them straight into the product card's reference images. Saves a manual paste/upload.
- **Browser-assisted scrape of product titles + images.** A small Playwright helper that opens the URL in the existing remote-debug Chrome, grabs the title and primary image, and pre-fills the product card. Avoids the API-blocking issue TikTok has against headless scrapers.
- **AI vision analysis of the reference image.** Pass the image bytes (not just the filename) to the AI provider's vision endpoint so the generated `image_prompt` can include product-specific cues (color, packaging type, branding visible in the photo). All three current providers support vision in the right model variants.
- **Per-product variant pinning.** Right now the project-level "1x variant" pin is set once per batch. Some products want 2x or 3x — make that a per-card flag.
- **Inline favorites display.** After `--sync-favorites`, show each row's flow_media_id thumbnail next to its CSV status in the Dashboard, so you can spot misbindings without leaving the UI.

## Mid-term (architectural)

- **SQLite as products store.** `products.json` is fine up to ~hundreds of products per batch but it doesn't scale to thousands or to multi-batch reporting. A single `data/state.sqlite` with `batches` and `products` tables would let us query across batches ("show me every video I shipped last week"). Wait until that's the bottleneck.
- **Packaged installer.** Currently the user needs Docker Desktop + a working PowerShell session. A signed Windows installer that bundles Python, the venv, and `docker compose pull` would lower the barrier to share with non-technical TikTok creators.
- **SaaS version.** Multi-tenant: each user runs their own remote-debug Chrome on their own machine, but the UI/CSV/AI calls happen on a hosted backend. Requires real auth, per-user secrets, and a way to ferry CDP traffic that doesn't require localhost — probably a tunnelled proxy. Big undertaking; only makes sense if there's product-market fit.

## Future / speculative

- **Detection of duplicate products** across batches (same TikTok URL or same `original_title`).
- **Hashtag library + reuse.** Track which hashtags performed for past batches; suggest hashtags from history in the AI output's `caption`.
- **Video-prompt variant generation.** One image, multiple motion variants (push-in vs. orbit vs. shelf drift). Lets you A/B without re-uploading.
- **Approval / rejection learning loop.** Feed the AI which `image_prompt`s ended up favorited vs. rejected; tune future prompts. Light supervised tuning.
- **Cost / token accounting.** Show per-batch token usage + estimated cost from each AI provider; warn before running a huge batch.
- **Webhook on completion.** Ping a Slack/Discord URL when a batch finishes video generation.

## Explicitly NOT planned

- **Running Chrome inside Docker.** Google blocks sign-in in Playwright's bundled Chromium and in headless Chrome behind a container. The "host Chrome + CDP proxy" architecture is the working solution and won't move.
- **Replacing the existing CLI flow.** The CLI is the canonical entry point; the UI is an authoring + monitoring layer on top.
