# Connected Runner (alpha)

The runner-side write-up for the **connected runner / polling** flow.
For the SaaS-side view (route reference, auth model, mode switch),
read [`flow-bof-saas/docs/CONNECTED_RUNNER.md`](../../flow-bof-saas/docs/CONNECTED_RUNNER.md).

## Why this exists

The hosted SaaS at `https://app.autobof.xyz` can't reach
`http://127.0.0.1:9444` on your laptop. The runner has to be the
one that initiates the connection. The connected-runner mode is a
long-lived loop that:

1. Authenticates with a per-agent Bearer token.
2. POSTs `/api/runner/health` to advertise itself.
3. POSTs `/api/runner/jobs/next` every few seconds and runs anything
   the SaaS hands back.
4. Streams progress events as it goes, then a final result envelope.

Existing flows are unchanged:

- `python main.py --agent-server` still boots the local HTTP API
  on `127.0.0.1:9444`. Use it when the SaaS and runner share a
  machine (local dev).
- `python main.py --agent-job <type>` still runs a one-shot job
  from the CLI.

The polling mode is *additive* — same `handle_agent_job` dispatch
table, same job envelopes.

## Setup

1. **Register an Agent in the SaaS.**
   Open the cockpit at your hosted URL → Runner page → fill in any
   name (Base URL doesn't matter for polling — the SaaS never dials
   the runner) → Register.

2. **Generate a runner token.**
   On that agent's card click **Generate runner token**. The token
   appears once. Copy it; the SaaS only ever stores a hash.

3. **Set environment on the runner machine:**
   ```bash
   export SAAS_BASE_URL=https://app.autobof.xyz
   export RUNNER_TOKEN=runner_xxxxxxxxxxxxxxxx
   # Optional knobs:
   # export RUNNER_POLL_INTERVAL_SECONDS=5
   # export RUNNER_HEALTH_INTERVAL_SECONDS=60
   # export RUNNER_HTTP_TIMEOUT_SECONDS=30
   ```

4. **Run the poller:**
   ```bash
   python main.py --runner-poll
   ```

   Inside Docker (compose `agent` profile):
   ```bash
   docker compose --profile agent run --rm \
     -e SAAS_BASE_URL \
     -e RUNNER_TOKEN \
     agent python main.py --runner-poll
   ```

   The first line you'll see is something like:
   ```
   [INFO] runner_poller: runner poller starting → https://app.autobof.xyz (poll=5.0s, health=60.0s)
   [INFO] runner_poller: capabilities: check_flow_connection, generate_flow_images, generate_flow_videos_from_favorites, health_check, scan_favorited_images
   [INFO] runner_poller: health ok → agent clx…; server time 2026-…
   ```

5. **Trigger a job from the SaaS.**
   Batch page → workbench → e.g. **Scan favorites** or **Generate
   videos**. With `APP_RUNNER_MODE=polling` (the production default)
   the SaaS just creates a Job at `status=queued`; the poller picks
   it up on its next tick.

## What the loop does, step by step

```python
while not stopping:
    if (time since last health) >= RUNNER_HEALTH_INTERVAL_SECONDS:
        POST /api/runner/health         # advertise + heartbeat

    job = POST /api/runner/jobs/next    # claim oldest queued job
    if job is None:
        sleep RUNNER_POLL_INTERVAL_SECONDS
        continue

    envelope = handle_agent_job(
        job,
        progress_callback=lambda evt: POST /api/runner/jobs/<id>/events
    )
    POST /api/runner/jobs/<id>/complete  # final envelope
```

The progress callback is the same one `--agent-job-json
--agent-progress-jsonl` uses, just wired to HTTP. Per-event POSTs
that fail are logged but never crash the job — progress is
informational.

## Dedicated Chrome profile

The runner launches Chrome with its own `--user-data-dir`:

- Windows: `%LOCALAPPDATA%\FlowBOF\ChromeProfile`
- macOS:   `~/Library/Application Support/FlowBOF/chrome-profile`

That profile is **separate from your normal Chrome**. Closing your
normal Chrome doesn't affect the runner; closing the runner's
Chrome window doesn't affect your normal browsing. The runner never
kills Chrome processes wholesale — it only operates on the Chrome
instances it spawned, against the dedicated profile.

### Closed the runner's Flow window by accident?

Two ways to bring it back without touching your normal Chrome:

- **Menu:** in the running runner, choose
  `Open/Reopen Google Flow browser`.
- **CLI:** `FlowBOFRunner --open-browser` (or
  `python runner_app.py --open-browser`).

The helper picks the cheapest option that works:

1. Activates an existing Flow tab in the dedicated profile if it
   finds one (no new Chrome process).
2. Opens a fresh Flow tab in the dedicated profile via the CDP
   `PUT /json/new` endpoint (still no new process).
3. Cold-launches the dedicated Chrome profile if the window was
   fully closed.

## Console lifecycle

The packaged runner runs in **console mode**:

- Windows: the `.exe` is built with `console=True`. Double-clicking
  opens a console window that stays open while the runner runs.
- macOS: double-click `Run Flow BOF Runner.command` to open the
  runner in a real Terminal window (a bare `.app` launched from
  Finder is silent — that's a macOS limitation, not a runner bug).

On error / Ctrl-C, the window does **not** auto-close — it prints
"Press Enter to exit." or "Runner stopped. Press Enter to exit." so
the operator can read what happened. Pass `--no-pause` (CI / build
smoke-tests) to skip the prompt.

While the runner is polling, the console keeps showing per-second
status, claimed-job lines, progress events, and the final
succeeded/failed status. Closing that console window stops the
runner and any in-flight job completes its current step before
exiting.

## Automatic Flow UI prep

Before every image or video submit the runner runs a centralised
prep pass against the dedicated Chrome profile. The goal is to
recover from Flow leaving stale UI state behind — open menus,
suggestion pills, agent panels — that would otherwise intercept
clicks on the prompt input or the Generate button.

The prep code lives in `src/flow_ui_prep.py` and is called by both
agent handlers (`_handle_generate_flow_images` and
`_handle_generate_flow_videos_from_favorites`), so the Docker /
CLI path, the connected runner, and the packaged exe / .app all
behave identically.

What happens, per job step:

1. **Dismiss overlays.** `Escape`, then mouse to the corner, then
   a bounded sweep of visible buttons whose `aria-label` / `title`
   matches `close|dismiss|cancel` and *doesn't* match anything
   destructive (delete / discard / leave / confirm). Any leftover
   Radix menu (`[data-radix-menu-content]`, `[role="menu"]`,
   `[role="dialog"]`) gets one more `Escape`.
2. **Close agent prompt pills.** Targets the Material Symbols
   `close` glyph inside agent-prompt chips. Same destructive
   guard.
3. **Toggle off Agent mode.** Flow's composer has an "Agent" pill
   next to the `+` button. When pressed (`aria-pressed="true"`),
   the Generate arrow runs Flow's *agent flow* instead of the
   standard image-generation flow we automate — the recorded
   selectors all assume the standard flow, so a pressed Agent
   pill silently breaks every submit. The prep clicks the pill
   if `aria-pressed="true"` and verifies it flips to `"false"`
   before continuing. Visible symptom this guards against: the
   "Hi <name> / What would you like to do?" landing screen with
   three preset action buttons.
4. **Verify generation settings.** Image jobs re-apply 9:16 / 1x /
   Nano Banana Pro by calling `_apply_project_settings` in
   `recorded_flow.py`.

   Video jobs additionally pin the composer's model to
   **Veo 3.1 - Lite** (image-to-video) — Flow occasionally lands
   sessions on *Omni Flash* (text-to-video), which silently
   discards the favorited reference image and produces a
   prompt-only animation. The pin happens right after the Animate
   menuitem click inside `perform_recorded_video_flow`, because
   the Video tab in the settings popover only exposes the model
   dropdown once the tile has been promoted into the composer.
   See `ensure_veo_lite_model()` in `flow_ui_prep.py`.

The prep is **best-effort**: any failure is logged and the job
proceeds. Each step's result is also emitted as a `flow_ui_prep`
JobEvent on the SaaS so a debug pass shows up in the job
timeline.

### Env switches

| Variable                            | Default | Effect when off                                                                                            |
| ----------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------- |
| `FLOW_UI_PREP_ENABLED`              | `true`  | Skip the entire prep pass.                                                                                 |
| `FLOW_DISMISS_OVERLAYS`             | `true`  | Skip the Escape / close-button / menu sweep.                                                               |
| `FLOW_ENSURE_GENERATION_SETTINGS`   | `true`  | Skip re-applying 9:16 / 1x / Nano Banana Pro.                                                              |
| `DEBUG_FLOW_PREP`                   | `false` | Log every step (no-ops included) at INFO. Useful when triaging a Flow UI regression.                       |

These are operator switches — not surfaced in the SaaS UI. Set
them on the runner shell / Docker env for one-off debugging.

### Video prompt

Video jobs always submit the **universal blanket video prompt**:

> Slow handheld iPhone-style push-in toward the product. A hand
> enters the frame and gently taps the product once, as if the
> person recording is checking it on the shelf. Preserve the exact
> product appearance. Keep the environment stable and realistic.
> No morphing, no dramatic camera move, no cinematic lighting.

The runner doesn't accept per-tile video prompts in the SaaS-driven
flow; the prompt is sourced from `config.py` regardless of what's
on the product row.

## What never crosses the boundary

- **Generated media.** Final videos + generated images stay in
  Google Flow. The runner reports back Flow media IDs / edit IDs /
  counts / errors — not the underlying video files. Users download
  finished videos directly from Flow.
- **Debug snapshots / screenshots.** Anything the runner saves under
  its own `data/` or `outputs/` dirs stays on the runner machine.
  Useful for troubleshooting locally; never uploaded to the SaaS.
- **Google / TikTok cookies.** They live in your debug Chrome
  profile and stay there. The runner doesn't even read them; the
  browser does, via CDP.
- **AI provider keys.** OpenAI / Anthropic / OpenRouter keys live
  on the SaaS server. They're used to write `imagePrompt` strings
  *before* a job is queued. Job payloads carry the finished prompt,
  never the key.
- **The runner token.** Stored in the SaaS as SHA-256 hex only.
  Logged only as `Authorization` header value; never printed.

## Troubleshooting

**`HTTP error on POST /api/runner/health`** — `SAAS_BASE_URL` is
wrong (typo, missing `https://`, trailing slash issues are
auto-stripped). Try `curl $SAAS_BASE_URL/api/health`; it should
return `{"ok": true, ...}` without basic auth.

**`POST /api/runner/health → 401 unauthorized. Check RUNNER_TOKEN.`** —
The token doesn't match any agent's hash, or the agent was deleted
in the SaaS, or you revoked the token there. Generate a new one
and replace `RUNNER_TOKEN`.

**Health says ok but `/jobs/next` always returns null.** — The Job
rows you're creating in the SaaS are bound to a *different* Agent.
Check the runner-token UI on `/agents`: the queued job's
`agentId` has to match the agent whose token you're using.

**`Capabilities` log line is empty.** — `known_job_types()` returned
nothing, which means the local repo's `_JOB_HANDLERS` table is
empty. Reinstall / git pull and try again.

**Ctrl-C takes seconds to exit.** — Expected. The loop's chunked
sleep yields every 0.5s. Any in-flight job *finishes* before the
process exits (so the SaaS sees a `complete`/`fail`, not a stale
`running` row).
