# SaaS architecture — design document

Forward-looking design for the eventual split of flow-bof-automation into:

- a **hosted SaaS** that owns user accounts, product data, AI prompt
  generation, queues, and dashboards;
- a small **local agent** that runs on the user's Windows/Mac machine
  and drives their host Chrome via the existing CDP-proxy automation.

Status: **design only**. The current Docker-based local app keeps working
unchanged while we evolve toward this architecture. See
[`MIGRATION_PLAN_TO_SAAS.md`](MIGRATION_PLAN_TO_SAAS.md) for the phased
rollout that gets us from here to there.

## Core principle

> **Hosted SaaS = brain. Local agent = hands.**

The cloud never sees a Google cookie. It never opens a Chrome window.
It never possesses anything that, if stolen, could log in to a user's
TikTok or Google account. All it does is hold product data and emit
job instructions.

The agent never does anything creative on its own. It receives a
discrete instruction ("submit this image prompt for product X with this
reference image"), executes it against the local Flow Labs tab, and
reports the result. No business logic, no AI calls, no user data
persistence beyond a small local job state file.

## High-level architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          HOSTED SaaS                                │
│                                                                     │
│  Next.js frontend   FastAPI / Next API   Postgres   Object store    │
│  ───────────────    ──────────────────   ────────   ────────────    │
│   - dashboard        - REST + WS endpoint - users    - reference    │
│   - product cards    - Kalodata importer  - orgs       images       │
│   - prompts UI       - AI providers       - batches  - generated    │
│   - batch builder    - job queue          - products   thumbnails   │
│   - run history      - queue dispatcher   - jobs                    │
│   - billing later    - assets uploader    - runs                    │
│                                                                     │
└──────────────────┬────────────────────────────────┬─────────────────┘
                   │                                │
                   │ HTTPS + WebSocket              │ HTTPS GET
                   │ (auth: agent token)            │ (signed URLs)
                   │                                │
                   ▼                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       USER'S LOCAL MACHINE                          │
│                                                                     │
│   ┌──────────────────┐         ┌──────────────────────────┐         │
│   │  Local agent     │ ─CDP─►  │  Host Chrome (debug)     │         │
│   │  (Python today,  │         │   - Flow Labs tab        │         │
│   │   exe/dmg later) │ ─CDP─►  │   - TikTok tab (later)   │         │
│   │                  │         │   - User's Google login  │         │
│   │  - cdp-proxy     │         │   - Cookies              │         │
│   │  - job runner    │         └──────────────────────────┘         │
│   │  - asset cache   │                                              │
│   │  - state.json    │                                              │
│   └──────────────────┘                                              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Hosted components

### Frontend (`saas/web/`, future)

- **Stack:** Next.js (App Router), React, Tailwind.
- **Responsibilities:**
  - Login / org switcher.
  - Batch builder UI (the BOF page in today's Streamlit app maps almost
    1:1 here, minus the embedded subprocess output).
  - Kalodata XLSX upload + parse preview.
  - Product card editor.
  - AI prompt generation (calls the backend, which calls the model).
  - Job queue dashboard: pending / running / done / failed.
  - Per-run log viewer (pulled from object storage).
  - Agent status pane: which agents are online, which org/workspace
    they're bound to, last heartbeat.
- **Why Next.js:** SSR for the dashboard, file uploads via signed URLs,
  WebSocket-aware for live agent status, easy auth-provider integration.

### Backend (`saas/api/`, future)

- **Stack:** FastAPI **or** Next.js API routes — pick when the team is
  staffed. FastAPI is the better default because the current Python
  business logic ports directly; Next.js API routes are reasonable if
  the team is JS-only and we re-implement the Kalodata importer in TS.
- **Responsibilities:**
  - REST: products, batches, prompts, runs, signed-URL minting.
  - WebSocket: agent connection (one per active agent install).
  - AI provider plumbing (OpenAI / Anthropic / OpenRouter). Same
    abstractions as today's `ai/providers/` — port unchanged.
  - Kalodata XLSX parser (port `src/kalodata_importer.py` as-is).
  - Job queue dispatcher: takes "generate images for batch X" and fans
    it out into per-row jobs.
  - Run aggregator: receives progress events from the agent, stores
    them in Postgres + writes the full log blob to object storage.

### Database (Postgres)

Tables (rough cut, schema in [`MIGRATION_PLAN_TO_SAAS.md`](MIGRATION_PLAN_TO_SAAS.md)):

| Table        | What it holds                                                   |
| ------------ | --------------------------------------------------------------- |
| `users`      | Auth identities. From Clerk / Supabase / Auth.js.               |
| `orgs`       | Workspaces. Billing entity later.                               |
| `org_members`| Many-to-many users ↔ orgs with roles.                           |
| `agents`     | One row per agent install (token, OS, last heartbeat, version). |
| `batches`    | Replaces today's `data/batches/<batch_id>/`.                    |
| `products`   | Replaces today's `products.json`.                               |
| `prompts`    | AI-generated image/video prompts. Per-product, versioned.       |
| `jobs`       | Queue rows: type, payload, status, assigned agent, attempts.    |
| `runs`       | Aggregated outcome of a job batch (the "Generate Images" click).|
| `assets`     | Pointers into object storage (reference images, manifests).     |

### Job queue

Two viable options:

- **Postgres-backed (e.g. `pg-boss`, `procrastinate`)** — simpler ops,
  fewer moving parts, fine to ~50 req/s. Recommended for MVP.
- **Redis + BullMQ** — better when we need fan-out across many agents.
  Move here when the queue gets hot or we need millisecond latency.

Either way the abstraction in the backend is "enqueue a job → mark it
done". The agent transport is independent of the storage choice.

### Object storage

- **Stack:** S3, Cloudflare R2, or Supabase Storage (all S3-compatible).
- **Buckets:** `reference-images/`, `manifests/`, `logs/`, `videos/`
  (the last one only if we ever download finished videos from Flow,
  which we currently don't).
- Signed URLs for both upload (frontend) and download (agent). No
  proxying through the API server.

### Auth

- **Stack:** Clerk, Supabase Auth, or Auth.js (NextAuth). Pick the one
  whose org/workspace primitives match what we want.
- Frontend auth = session cookie.
- Agent auth = **opaque token** issued when the user clicks "Connect
  agent" in the dashboard. The agent stores it locally; SaaS rotates
  on demand.

## Local agent

Full design in [`LOCAL_AGENT_ARCHITECTURE.md`](LOCAL_AGENT_ARCHITECTURE.md).
Sketch:

- **Language:** Python (today) → eventually packaged via PyInstaller or
  wrapped in Tauri so it ships as `agent.exe` / `agent.dmg`.
- **Surface:** a tiny tray app + a localhost HTTP API + a WebSocket
  back to the SaaS.
- **State on disk:** `~/.flow-bof-agent/state.json` (job cache,
  per-job timing, last heartbeat). No user-data.
- **Reuses the current Python code:** `src/recorded_flow.py`,
  `src/flow_tiles.py`, `src/flow_automation.py`, `src/manifest_workflow.py`
  port over unchanged. The CDP-proxy nginx container ships with the
  agent installer.

## Data flow — image generation, end to end

```
1. User uploads Kalodata XLSX in the SaaS dashboard.
   └─► API parses, writes `products` + signed-URL upload for each
       reference image.

2. User clicks "Generate prompts".
   └─► API calls OpenAI/Anthropic/OpenRouter (same providers as today),
       stores results in `prompts`. Frontend renders the prompts.

3. User clicks "Generate Images".
   └─► API enqueues a `generate_flow_images` job with:
        - batch_id
        - list of (product_id, image_prompt, reference_asset_id)
        - signed URLs for each reference image

4. Agent picks up the job over WebSocket.
   └─► Agent downloads reference images via signed URLs to local cache.
   └─► Agent drives the user's Chrome (which is logged into Flow) to
       submit each prompt, captures the resulting tile_id + media_id.
   └─► Agent emits progress events back over WebSocket
       ("submitted product X (tile=Y)", "captured media_id Z").

5. User hearts favorites in Flow's UI.
   └─► No backend interaction here. Flow's own state.

6. User clicks "Generate Videos from Favorited Images".
   └─► API enqueues `generate_flow_videos_from_favorites` for the batch.
   └─► Agent scans Flow grid for hearted tiles, animates each with the
       blanket video prompt, marks them as submitted in local
       `video_submitted_tiles.json` (kept on the user's machine to
       de-dup across runs).

7. (Future) TikTok draft creation.
   └─► API enqueues `create_tiktok_draft_later` jobs once we have a
       reliable post path. Same agent, different selectors.
```

The browser is the source of truth for "did Flow actually accept this
prompt?" — we don't reconcile in the SaaS. The agent reports observed
state.

## Why browser automation stays local

Every alternative we considered fails on at least one of these:

1. **Headless Chrome in cloud.** Google blocks login from datacenter
   IPs and Playwright's fingerprintable Chromium. Users would have to
   solve captchas the agent can't see. Also forbidden by Google ToS.
2. **Forward Chrome cookies to a cloud-side browser.** Cookies are
   session-bound to user-agent + IP fingerprint; transplanting them
   gets you logged out within minutes. Plus: now we hold a credential
   we can't legally store.
3. **Use Flow's official API.** Doesn't exist.
4. **Have the SaaS proxy CDP commands directly to user's Chrome.**
   Requires the user's machine to expose port 9222 to the internet —
   security disaster. CDP is unauthenticated.

The local agent is the only path that respects "we never see Google
credentials." The agent stays on the user's hardware; the SaaS just
sends it instructions and receives status.

## Security model

| Concern                                | Mitigation                                      |
| -------------------------------------- | ----------------------------------------------- |
| Google credential exfiltration         | Cookies + profile dir live in user's filesystem only. The agent NEVER reads from the Chrome profile directory itself — it only talks to the running browser via CDP. Profile contents are not uploaded. |
| SaaS-side breach exposing user content | SaaS holds product data + prompts. No Google credentials, no Chrome profiles, no API tokens for the user's external accounts. Worst case = a tester's product list leaks. |
| Stolen agent token                     | Token is opaque, per-install, revokable in the dashboard. Bound to one org. Rotation forces re-link. No long-lived AWS credentials inside the agent. |
| Local agent compromised                | Agent has filesystem access already; new threat surface is small. Tokens have a server-side scope ("operate as agent for org X"). They cannot read other users' data. |
| Network MITM                           | All traffic over TLS. Token sent in `Authorization: Bearer …` header. Agent pins SaaS root certificate optionally. |
| Replay of completed jobs               | Each job carries a server-issued ULID; agent refuses to re-execute a job it has already marked done in `~/.flow-bof-agent/state.json`. |
| Sensitive logs                         | Agent never logs cookies or auth headers. Per-run log blobs uploaded to object storage are redacted client-side before upload. |

## Future TikTok posting flow

This is the long-term reason the agent exists at all. Sketch:

1. SaaS schedules `create_tiktok_draft_later` jobs against a calendar
   that the user defines ("3 posts/day at 8am/2pm/8pm").
2. Each job carries: which Flow video to use (Flow video URL or
   media_id), caption, hashtags, optional product link.
3. Agent at firing time:
   - Switches the user's host Chrome to the TikTok Studio tab (or
     opens a new one).
   - Uses the user's existing TikTok login (already in that Chrome
     profile).
   - Uploads the video, fills the caption, saves as draft.
   - Reports back: draft_id, posted_at.
4. The user reviews + publishes drafts manually for the alpha; full
   auto-publish is a future flag.

Same security model: TikTok cookies stay in the user's Chrome.

## What lives in the SaaS vs. agent vs. user's Chrome

| Concern                                | SaaS | Agent | Browser |
| -------------------------------------- | :--: | :---: | :-----: |
| User account / billing                 | ✅   |       |         |
| Org / workspace data                   | ✅   |       |         |
| Product metadata (name, description)   | ✅   |       |         |
| Reference images                       | ✅   | cache |         |
| AI-generated prompts                   | ✅   |       |         |
| Job queue                              | ✅   |       |         |
| Run history + log blobs                | ✅   | cache |         |
| Google credentials / cookies           |      |       | ✅      |
| TikTok credentials / cookies           |      |       | ✅      |
| Generated tiles / videos               |      |       | ✅      |
| `video_submitted_tiles.json` (de-dup)  |      | ✅    |         |
| Per-row CSV state                      |      | ✅    |         |
| Manifest file                          |      | ✅    |         |
| User's AI API keys (alpha)             |      | ✅    |         |
| User's AI API keys (managed plan)      | ✅   |       |         |

## Open questions to answer before Phase 3

1. **Do we host AI keys centrally** (managed billing) or **stay BYOK**?
   - BYOK = simpler, no liability, no GPT-API margin.
   - Hosted = better UX, recurring revenue, simpler onboarding.
2. **One agent install per user, or per org?** Probably per user, but
   shared agents (a "shared workstation in the office") would be nice.
3. **Multi-tenant agent.** Can one agent serve multiple orgs? Probably
   yes via token switching, but defer to v2.
4. **Workspace-level AI budgets** — needed if we ever go managed.
5. **What happens when the agent is offline?** Jobs queue, dashboard
   shows "waiting for agent", user gets a notification when their
   agent reconnects.
