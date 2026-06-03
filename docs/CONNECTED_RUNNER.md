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
