# Local agent — architecture

The agent is a small program that runs on the user's Mac or Windows
machine. It controls the user's host Chrome via the same CDP-proxy
pattern the current Docker app uses, but it takes its instructions
from the hosted SaaS instead of from a local Streamlit UI.

Status: **design only**. Lives next to the existing code but won't
disrupt it. See [`MIGRATION_PLAN_TO_SAAS.md`](MIGRATION_PLAN_TO_SAAS.md)
for the phase-by-phase landing.

## Responsibilities

| Concern                              | In scope for agent?         |
| ------------------------------------ | --------------------------- |
| Launch user's Chrome in debug mode   | ✅                          |
| Maintain the CDP proxy               | ✅ (ships with the agent)   |
| Drive Flow Labs via Playwright       | ✅ (reuses today's code)    |
| Heart-favorite tile scanning         | ✅                          |
| Submit videos for favorited tiles    | ✅                          |
| Future TikTok draft creation         | ✅                          |
| Receive jobs from SaaS               | ✅                          |
| Download reference images from SaaS  | ✅                          |
| Report progress + final results      | ✅                          |
| Hold Google / TikTok credentials     | ❌ (live in Chrome profile) |
| Persist product / batch data         | ❌ (lives in SaaS)          |
| Call AI providers                    | ❌ (SaaS does it)           |
| Generate AI prompts                  | ❌                          |
| Re-implement Kalodata XLSX parsing   | ❌                          |
| Auth UI for end users                | ❌ (browser-based)          |

The agent's mantra: **one job at a time, execute it, report back.**

## How it controls Chrome

Unchanged from the current Docker app. The agent ships the same
`cdp-proxy` (nginx) component the current `docker-compose.yml`
deploys, plus the Chrome-launch script from `scripts/start_chrome_debug.*`.

```
   Agent (Python)
       │
       │   Playwright connect_over_cdp("http://127.0.0.1:9333")
       ▼
   cdp-proxy (nginx on 9333)
       │
       │   forwards to 127.0.0.1:9222 (or host.docker.internal:9222
       │   when the proxy itself runs in Docker)
       ▼
   Host Chrome (--remote-debugging-port=9222)
       │
       └── flows.google.com tab, TikTok tab, etc.
```

Two reasons the proxy stays:

1. Chrome 116+ rejects CDP WebSocket handshakes whose `Origin` header
   isn't an explicit allow-list match. The proxy rewrites the header.
2. Chrome's `/json/version` endpoint hard-rejects requests whose `Host`
   header isn't `127.0.0.1`/`localhost`. The proxy rewrites that too.

Three deployment options for the proxy:

| Mode             | When                                      |
| ---------------- | ----------------------------------------- |
| Bundled Docker   | Beta — easiest, mirrors today's setup.    |
| Native nginx     | If we want a fully-no-Docker install.     |
| Pure Python      | Tiny in-process forwarder (~50 LOC). Best for the polished release. Removes the Docker requirement entirely. |

We'd ship Docker-bundled in Phase 6 and migrate to pure-Python in a
later release once we're confident in the rewritten forwarder.

## How it talks to SaaS

Default transport: **persistent WebSocket** to a SaaS endpoint like
`wss://app.example.com/v1/agent/connect?token=<agent_token>`. JSON
messages in both directions.

| Channel | Direction              | Used for                                     |
| ------- | ---------------------- | -------------------------------------------- |
| WS      | SaaS → agent           | New job, cancel job, ping, force-update.     |
| WS      | Agent → SaaS           | Heartbeat, job progress, job result, errors. |
| HTTPS   | Agent → SaaS           | Initial registration, token refresh, log blob upload. |
| HTTPS   | Agent → object store   | Reference image download (signed URLs).      |

### WebSocket vs polling

|                                   | WebSocket                                        | Polling                                           |
| --------------------------------- | ------------------------------------------------ | ------------------------------------------------- |
| Latency to dispatch a job         | ~ms (server pushes)                              | up to the poll interval (1–10 s usually)          |
| Server cost at idle               | constant connection per agent — fine to ~10k    | request per poll per agent — cheaper at low scale |
| Firewall friendliness             | Most NATs forward outgoing TCP fine; some pickier corp networks block long-lived WS | trivially passes any firewall                     |
| Server complexity                 | Need a WS handler + connection registry          | None — just an HTTP endpoint                      |
| Reconnection handling             | Required (network drops, laptop sleep)           | Implicit — each poll is independent               |
| Cancellation latency              | ~ms                                              | up to poll interval                               |

**Recommendation:**
- **MVP (Phase 5):** polling. The agent hits `GET /v1/agent/jobs/next`
  every 5–10 s. Dead simple, no WS handler on the server, works
  through every corporate firewall. The user experience is "click
  Generate, wait up to 10s for the agent to pick it up" — fine for an
  alpha.
- **Polish (Phase 7+):** WebSocket. Add server-push for instant
  dispatch + agent presence. Keep the polling endpoint as a fallback
  for agents that can't establish WS.

### Heartbeats

Every 30 s (WS) or every poll (polling):
```json
{
  "agent_id": "01JABCD...",
  "agent_version": "0.4.2",
  "os": "darwin-arm64",
  "chrome_status": "reachable",
  "flow_logged_in": true,
  "last_completed_job_id": "01JABCE..."
}
```
SaaS uses this to render the "Agent online" badge in the dashboard.

## Local job runner design

The runner is a single in-process event loop. One job at a time —
the user's Chrome is a single resource and parallelism causes the
exact race conditions we've spent the alpha fighting.

```
┌──────────────────────────────────────────────────┐
│  agent main loop                                 │
│                                                  │
│    while True:                                   │
│        job = await get_next_job()                │
│        try:                                      │
│            for evt in execute(job):              │
│                await report(evt)                 │
│        except Exception as e:                    │
│            await report_error(job, e)            │
│        await save_state()                        │
└──────────────────────────────────────────────────┘

execute(job) is a generator that yields progress events.
get_next_job() either:
  - awaits a "new_job" frame on the WS, or
  - polls GET /v1/agent/jobs/next?wait=10
```

### Handler dispatch

Each `job_type` from [`JOB_PROTOCOL.md`](JOB_PROTOCOL.md) maps to a
single handler function:

```
JOB_HANDLERS = {
    "health_check":                       handle_health_check,
    "check_flow_connection":              handle_check_flow_connection,
    "import_assets":                      handle_import_assets,
    "generate_flow_images":               handle_generate_flow_images,
    "scan_favorited_images":              handle_scan_favorited_images,
    "generate_flow_videos_from_favorites":handle_generate_flow_videos_from_favorites,
    "download_flow_videos":               handle_download_flow_videos,
    "create_tiktok_draft_later":          handle_create_tiktok_draft_later,
}
```

Each handler is a generator that yields `JobProgressEvent` JSONs as it
goes and ends by yielding a final `JobResult` event. The runner
serializes and forwards each one to the SaaS.

### Reuse of current code

These map almost 1:1:

| Existing module                                | Handler that calls it                                            |
| ---------------------------------------------- | ---------------------------------------------------------------- |
| `src/recorded_flow.py:perform_recorded_flow`   | `handle_generate_flow_images`                                    |
| `src/recorded_flow.py:perform_recorded_video_flow` | `handle_generate_flow_videos_from_favorites`                 |
| `src/flow_tiles.py:scan_tiles`                 | `handle_scan_favorited_images`                                   |
| `src/flow_automation.py:open_flow_browser`     | every handler that needs a browser                               |
| `src/manifest_workflow.py:run_generate_videos_from_favorited_tiles` | trim down to a generator and reuse                  |
| `src/health.py`                                | `handle_health_check`                                            |
| `src/video_state.py`                           | the agent keeps this exact file at `~/.flow-bof-agent/video_submitted_tiles.json` |

The Python automation code is the asset we're preserving across the
SaaS split. None of it needs to be rewritten.

## Job lifecycle

```
   queued (SaaS)
      │
      │  agent pulls or receives via WS
      ▼
   assigned (SaaS) / received (agent)
      │
      │  handler starts; periodically yields progress
      ▼
   in_progress
      │
      ├─► progress event ─► SaaS updates run dashboard
      │
      │  handler returns
      ▼
   succeeded  OR  failed
      │
      │  SaaS persists, frontend updates
      ▼
   archived (after N days)
```

A job is never silently dropped. If the agent crashes mid-job:

1. SaaS-side timeout (e.g. 30 minutes since last progress) flips
   `assigned` → `expired`.
2. When the agent reconnects it sends a "still alive" frame and SaaS
   responds with any orphaned jobs. The agent either resumes (if it
   recorded a checkpoint in `state.json`) or refuses and reports
   `interrupted`.
3. Failed/expired jobs surface in the dashboard with a "Retry" button.

## Error handling

Three failure tiers, each handled differently:

| Tier                                 | Example                                           | Behavior                                                       |
| ------------------------------------ | ------------------------------------------------- | -------------------------------------------------------------- |
| **Transient (retry inline)**         | Flow tile rendered slowly; "Add to Prompt" disabled for 45s. | Handler does its own retry/timeout. Reports `progress` with note. |
| **Row-level (skip + continue)**      | One product's upload failed; 29 others to go.     | Handler emits `progress` with `{"failed_item_id": ...}` and moves on. Job overall still succeeds. |
| **Job-level (abort)**                | Chrome is unreachable; user logged out of Flow.   | Handler raises. Runner emits `job_failed` with diagnostic.     |

The current per-row try/except logic in `manifest_workflow.py` already
does the row-level tier. We carry it over.

## Local file handling

Single directory: `~/.flow-bof-agent/`

```
~/.flow-bof-agent/
├── config.json                  # agent token, SaaS endpoint, agent_id
├── state.json                   # last heartbeat, in-flight job id
├── video_submitted_tiles.json   # de-dup state (mirrors current file)
├── cache/
│   └── reference-images/
│       └── <asset_id>.jpg       # downloaded reference images, GC'd
├── logs/
│   └── 2026-06-02.log           # per-day rotating logs
└── chrome-profile/              # ONLY if we package Chrome too; otherwise
                                 # the user's own Chrome profile lives at
                                 # ~/chrome-flow-automation (today's path)
```

- **No PII outside `chrome-profile/`.** The browser profile dir is the
  one place Google's cookies live, and the agent treats it as opaque
  storage — never reads its contents directly.
- **Reference-image cache** GC'd by job; once a job is archived in the
  SaaS, the agent purges its cache entries.
- **Logs** rotated daily, capped at 30 days. Optionally uploaded to
  the SaaS as a compressed blob when a job fails.

## Agent update strategy

Three approaches in order of polish:

1. **Manual** (alpha): same `./update.sh` / `.\update.ps1` workflow as
   today. Agent reads `agent_version` from a checked-in file, SaaS
   refuses to dispatch jobs to incompatible versions, dashboard tells
   the user to update.
2. **Self-update** (beta): on heartbeat, SaaS responds with
   `latest_agent_version`. If newer, agent downloads the signed
   installer + verifies signature + relaunches. Mac uses `pkg`,
   Windows uses `msix` or signed installer.
3. **Squirrel-style differential update** (later): only if release
   cadence demands it.

### Compatibility contract

A job's `protocol_version` is set by the SaaS. Agents declare which
protocol versions they understand at registration. If a user is on
agent v1 and the SaaS issues v2 jobs:
- Agent reports "incompatible protocol" and the SaaS routes the job
  to a queue waiting for v2 agents (or never enqueues it).
- Dashboard shows "Update your agent to use this feature."

This way we never silently fail a job because the agent didn't know
what to do.

## What the agent doesn't do

- No login UI. The user pastes a one-time pairing code from the SaaS
  dashboard into the agent's tray menu on first run, and that's it.
- No batch creation. The dashboard owns that.
- No AI calls. The SaaS calls OpenAI / Anthropic / OpenRouter and ships
  the resulting prompt strings inside the job payload.
- No persistent product data. If the agent is reinstalled fresh, the
  user loses nothing.
- No telemetry beyond job-progress + heartbeat. Crash reports are
  opt-in.

## Open implementation questions

1. **Single-binary or Python runtime?** PyInstaller bundle is the
   simplest; the user double-clicks `agent.exe`. Downsides: 60 MB,
   Windows Defender warnings until we sign it. Tauri wrapper is more
   polished but means writing a Rust shell.
2. **System-tray UX.** Mac: `pystray` or native via Tauri. Windows:
   the same. What does "open the agent" do — popup a settings panel,
   or just bounce the user to the SaaS dashboard?
3. **Auto-launch Chrome at boot?** Probably not — we want the user to
   explicitly start the session so Chrome is "theirs" while they
   work.
4. **How aggressive is auto-pause-when-the-user-is-typing?** Today the
   automation is foreground-pauseable; the local agent loses that
   ergonomic. Maybe register a global hotkey "pause".
