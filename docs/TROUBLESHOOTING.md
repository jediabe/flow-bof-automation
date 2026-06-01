# Troubleshooting

In rough order of "things that bite testers in the first hour".

The **Setup → Run health check** button in the UI tells you which of these
applies. Re-run it after fixing anything.

---

## Docker Desktop not running

**Symptom**
- `setup.ps1` or `start.ps1` prints `error during connect: ... open //./pipe/docker_engine`.
- Or just hangs.

**Fix**
1. Start Docker Desktop from the Start menu.
2. Wait for the whale icon in the system tray to stop animating.
3. Re-run the script.

---

## Chrome debug not reachable

**Symptom (in the UI Setup health check)**
- `Chrome remote debugging — Chrome debugger unreachable at http://cdp-proxy:9333`

**Most common cause**: Chrome was already running when `start.ps1` launched a new
window. Windows hands the new launch off to the existing Chrome, which silently
ignores the `--remote-debugging-port` flag. The new window opens; the debug
port doesn't.

**Fix**
1. Close **every** Chrome window (check the tray for hidden ones).
2. Run `.\start.ps1` again. The chrome script will warn if it still sees Chrome.

If the health check still fails, manually verify:
```powershell
curl http://127.0.0.1:9222/json/version
```
If that 200s, Chrome is up; the problem is between the UI container and the
CDP proxy. `docker compose logs cdp-proxy` will show it.

---

## Flow not logged in

**Symptom**
- The Generate Images run opens Flow but goes nowhere; the log says
  "no new project button found" or similar.

**Fix**
1. In the **same Chrome window** `start.ps1` opened, navigate to
   <https://labs.google/flow>.
2. Log in with your Google account.
3. Re-run Generate Images.

Flow's login cookie lives in the chrome-flow-automation profile, so you only
need to do this once until the profile is cleared.

---

## CDP proxy errors

**Symptom**
- `docker compose logs cdp-proxy` prints 502s or "connection refused".

**Fix**
- The cdp-proxy container forwards `cdp-proxy:9333` → `host.docker.internal:9222`.
  If Chrome is down on the host, every request 502s. Restart Chrome via
  `start.ps1`.
- If Docker Desktop has been freshly installed, `host.docker.internal` resolution
  needs Docker Desktop's WSL2 backend running (which is the default).

---

## Missing API key

**Symptom**
- The UI Setup banner shows `AI provider — openai: OPENAI_API_KEY not set`.
- The Generate Prompts step in BOF Batch Builder warns "AI provider not
  configured".

**Fix**
- Open **Setup** in the sidebar, paste your key, click **Test API key**, then
  **Save settings**.
- See [`API_KEYS.md`](API_KEYS.md) if you don't yet have an API key.

---

## OpenRouter model blank / default behavior

**Symptom**
- Test API key passes with the note "(using openrouter/auto — set OPENROUTER_MODEL
  to lock a specific model)".

**What this means**
- OpenRouter's auto-router picks a model per request. Costs and output style
  are unpredictable.

**Fix (optional)**
- In Setup, set `OpenRouter model` to a specific value:
  `anthropic/claude-3.5-sonnet`, `openai/gpt-4o-mini`, etc.

---

## Kalodata image download failed

**Symptom**
- After uploading a Kalodata `.xlsx`, the product card shows up but the
  reference image is missing.

**Cause**
- Kalodata image URLs sometimes 403 from the UI container.

**Fix**
- Use the **Drop an image** uploader or the clipboard paste button on the
  product card to attach an image manually.

---

## Unmatched favorited images

**Symptom**
- After **Sync Favorites**, the "Unmatched Favorited Images" section lists
  some or all of your hearts.

**Cause**
- You generated a variant inside Flow that wasn't tracked by the tool (you
  used Flow's regenerate button directly, or the row's `media_id` wasn't
  captured at submit time).

**Fix**
- The Unmatched section now back-fills via tile_id on the next sync, so just
  click **Sync Favorites** again first. If it's still unmatched, use the
  **Bind to product** dropdown next to the thumbnail.

If **every** favorite is unmatched, see also the "fast-submit aftermath"
fix in `src/sync_workflow.py:_backfill_media_ids_from_tile_ids`.

---

## Video tile menu failed

**Symptom**
- Log message like `menu did not appear after right-click — retrying`.

**Cause**
- The Add-to-Video sub-menu sometimes doesn't open on the first click (Flow
  Labs is a Radix UI app and menu state is fiddly).

**Fix**
- The tool already retries with multiple click strategies. If it eventually
  fails:
  1. Set `AUTOMATION_MODE=balanced` in `.env` (or in the Advanced panel in
     the UI) to lengthen hover/click waits. (`safe` mode was retired —
     legacy values are coerced to `balanced` automatically.)
  2. Re-run the video step.
  3. If it still fails, that tile may have a broken state in Flow — open it
     manually and generate the video by hand.

---

## "'Add to Prompt' button stayed disabled for 45s"

**Symptom**
- During image generation a single row fails with that exact error
  string, or a Playwright trace showing the `Add to Prompt` button
  in `disabled` state across many retries.

**Cause**
- Flow's backend hasn't finished processing the file you just uploaded.
  The `+ → Add to Prompt` button stays disabled until upload processing
  completes. Most uploads clear in well under a second; occasional slow
  ones can take 30 s+ (large file, server hiccup, image format that
  needs a server-side transcode).

**Fix**
- Re-run the row. The wait budget is already 45 s; if Flow needs longer
  than that the upload likely failed and a retry will start fresh.
- If a specific product fails repeatedly, drop a smaller or differently-
  formatted reference image on that card and try again.

---

## Video generation seems to use the wrong prompt

**Symptom**
- Your product cards show carefully authored video prompts but the videos
  Flow produces look generic.

**Cause**
- The app currently uses one universal video prompt for every product to
  prevent mismatches when users regenerate or manually favorite alternate
  image variants. Per-product `video_prompt` is reserved for a future
  advanced mode.

**Fix / opt-out**
- Open **6. Generate videos** in the BOF Batch Builder. The blanket prompt
  is shown there in an editable text area — change it and click **Save**.
- To switch to per-product prompts (alpha-fragile): set
  `USE_BLANKET_VIDEO_PROMPT=false` in `.env` and restart the UI.
- To verify in logs: each video run logs
  `Using blanket video prompt for product <id>`.

---

## "Unmatched Favorited Images" thumbnails are broken

**Symptom**
- The thumbnail shows a broken-image icon with alt-text like `0`.

**Cause**
- An older version of the UI used `st.image()` which made the Streamlit
  container fetch the labs.google URL — and the container has no Flow
  session cookie, so the fetch failed.

**Fix**
- Already fixed: the UI now renders inline `<img>` so your browser does
  the fetch with its existing auth. If you see this on the current build,
  hard-reload the page (Ctrl-Shift-R).
