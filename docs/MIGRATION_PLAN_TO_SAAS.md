# Migration plan: local Docker → SaaS + local agent

Goal: ship the SaaS + agent split without breaking the current
Docker-based alpha. Each phase is a checkpoint that can stand on its
own — if Phase N is the last one we ship for a while, the alpha
still works.

## Phase 0 — current local app (we are here)

**Status:** released as alpha.

**Surface:**
- Streamlit UI inside the `ui` Docker container.
- CDP-proxy `nginx` container.
- Python CLI inside the `app` Docker container (run via `docker compose run --rm app …`).
- Host Chrome with `--remote-debugging-port=9222`.
- User data in `data/`, `inputs/`, `outputs/`.

**Constraint:** **everything below this point must keep this working**
unchanged. Until we explicitly retire the Docker app, every existing
CLI command must still run, every `setup.sh` / `start.sh` / `update.sh`
must still work, and every product card in `data/batches/` must still
load.

## Phase 1 — extract automation functions into a stable agent interface

**Goal:** create the seam between "what the agent does" and "who calls it"
without changing observable behavior.

**What we change:**
- Add a new file: `src/agent_api.py`. Each public function corresponds to
  exactly one job type from [`JOB_PROTOCOL.md`](JOB_PROTOCOL.md):

  ```python
  def handle_health_check() -> AgentResult: ...
  def handle_check_flow_connection(params) -> Iterator[AgentEvent]: ...
  def handle_generate_flow_images(params) -> Iterator[AgentEvent]: ...
  def handle_scan_favorited_images() -> Iterator[AgentEvent]: ...
  def handle_generate_flow_videos_from_favorites(params) -> Iterator[AgentEvent]: ...
  ```

  These are **thin wrappers** around the existing functions in
  `src/manifest_workflow.py`, `src/recorded_flow.py`,
  `src/flow_tiles.py`, etc. No business logic moves; they just adapt
  the parameter shape to the JSON contract in `JOB_PROTOCOL.md` and
  yield progress events instead of calling `logger.info(...)`.

- Refactor `manifest_workflow.run_generate_videos_from_favorited_tiles`
  to yield events instead of printing them. The current CLI keeps
  working — it just consumes the iterator and prints each event.

**What we don't change:**
- `main.py` argparse surface.
- `streamlit_app.py` page logic.
- Manifest format, CSV columns, Docker compose layout.

**Acceptance:** every existing CLI command still runs to completion
with the same exit code and visible output as before.

**Effort:** ~1 week. Mostly mechanical refactor + thorough log
re-emission.

## Phase 2 — local agent HTTP API

**Goal:** the agent becomes addressable as a local HTTP service.
Streamlit talks to it instead of subprocessing `main.py` directly.

**What we change:**
- Add `agent/agent_http.py` — a small FastAPI app on `127.0.0.1:9444`
  exposing `POST /v1/jobs` and `GET /v1/jobs/<id>`. Routes call into
  `src/agent_api.py` from Phase 1.
- Update Streamlit's `run_cli(args)` helper to optionally talk HTTP
  instead of spawning subprocesses. Feature-flagged.
- Add a tiny launcher: `agent/bin/agent` (Python script, later
  packaged) that starts the HTTP service.

**What we don't change:**
- Docker layout (the agent still ships as part of the `app` container
  during this phase — packaging happens in Phase 6).
- Job persistence: at this phase a job lives in memory only.

**Acceptance:** flipping the feature flag in Streamlit makes "Generate
Videos" produce identical results, with identical timing, but the
subprocess output goes through the HTTP API instead.

**Effort:** ~1 week. The current synchronous subprocess pattern maps
to a synchronous HTTP request + Server-Sent Events for progress.

## Phase 3 — hosted backend prototype

**Goal:** stand up the SaaS API without any user-facing surface yet.

**What we change:**
- New repo? Decide at the start of this phase (see "Repo split" below).
  Tentatively a sibling: `flow-bof-saas/`.
- Postgres schema for `users`, `orgs`, `agents`, `batches`, `products`,
  `prompts`, `jobs`, `runs`, `assets`.
- FastAPI service with `POST /v1/agents/register`, `GET /v1/agent/jobs/next`,
  `POST /v1/agent/jobs/<id>/progress`, `POST /v1/agent/jobs/<id>/result`.
- Object storage bucket for reference images + manifests + log blobs.
- Postgres-backed queue (`pg-boss` or hand-rolled).
- AI provider plumbing — port `ai/providers/` over verbatim.
- Kalodata importer — port `src/kalodata_importer.py` verbatim.

**What we don't change:**
- Local Docker app is still the only way the user actually does work.

**Acceptance:** end-to-end test from a `curl` script that creates a
batch in the cloud, enqueues a fake job, and watches the row land in
Postgres. No agent involved yet.

**Effort:** ~2–3 weeks. Most of the time is infrastructure (auth,
Postgres migrations, S3 setup).

## Phase 4 — hosted frontend prototype

**Goal:** the dashboard exists and can do everything the Streamlit UI
does, minus the buttons that talk to an agent.

**What we change:**
- Next.js app: login, batches list, batch detail, product cards,
  Kalodata upload, prompts review, jobs list.
- Calls into the Phase 3 API.

**What we don't change:**
- No agent integration yet — the "Generate Images" button is disabled
  (or shows "agent not connected").

**Acceptance:** users can author batches in the cloud, generate AI
prompts, and see them — but can't execute against Flow yet.

**Effort:** ~3–4 weeks. UI port + design polish.

## Phase 5 — agent connects to SaaS job queue

**Goal:** the cloud can dispatch jobs and the agent runs them.

**What we change:**
- Agent gets a "Connect to SaaS" pairing flow: SaaS dashboard shows a
  6-digit code, user pastes it into the agent tray menu, agent
  registers and stores its token.
- Agent's main loop: poll `GET /v1/agent/jobs/next?wait=10`. When a
  job arrives, dispatch to the matching `handle_*` from Phase 1.
- Progress events post back over HTTPS.
- Frontend gets a live "running" view of each job by polling
  `GET /v1/jobs/<id>` every couple of seconds.
- Polling is fine for MVP — see [`LOCAL_AGENT_ARCHITECTURE.md`](LOCAL_AGENT_ARCHITECTURE.md) for the WebSocket upgrade path.

**What we don't change:**
- Old Docker app still works in parallel. We don't remove anything.
- AI keys still BYOK (the user pastes them into the dashboard, and
  the SaaS uses them at AI generation time only — never persists in
  plaintext).

**Acceptance:** a tester does the full BOF flow end-to-end through
the cloud dashboard, with the agent running on their laptop. No
Streamlit involvement.

**Effort:** ~2 weeks. Most of the agent code is already written
(Phases 1–2); the new work is the pairing flow, token persistence,
and reconnect logic.

## Phase 6 — package agent as exe/dmg

**Goal:** the agent is a one-click install.

**What we change:**
- PyInstaller (initial) or Tauri-wrapped (polished) bundle.
- Mac: signed `.dmg` (Apple Developer ID), notarized.
- Windows: signed `.msix` or signed `.exe` installer.
- The bundled agent ships the CDP proxy as in-process Python (no
  Docker dependency anymore).
- Installer puts an entry in the OS auto-start list (opt-in during
  install).
- Self-update path lights up: on each heartbeat the SaaS responds with
  `latest_agent_version`; the agent downloads + verifies + relaunches
  on a newer version.

**What we don't change:**
- The Phase 5 cloud dashboard.
- Old Docker app is still officially supported but we start
  recommending the new installer for new testers.

**Acceptance:** a fresh tester on a fresh Mac/Windows machine can
double-click an installer, log in to the SaaS, scan a Flow page, and
finish their first BOF batch — no Docker, no PowerShell, no shell
scripts.

**Effort:** ~3 weeks. Most of it is signing, code-signing
certificates, and the cross-platform installer matrix.

## Phase 7 — billing / licensing / managed AI

**Goal:** turn the SaaS into a real product.

**What we change:**
- Stripe (or LemonSqueezy) for subscriptions.
- Plan tiers: free (1 batch / month), pro (unlimited), team (multi-user).
- Optional managed AI: SaaS holds the OpenAI/Anthropic keys and bills
  per-token to the user's plan. BYOK still supported on the lowest
  tier.
- Per-org workspace limits enforced server-side.
- Org member roles (admin / member).

**What we don't change:**
- The agent. Its protocol is stable from Phase 5 forward.

**Acceptance:** a non-tester can pay for the product and use it.

**Effort:** ~3–4 weeks. Mostly Stripe integration + billing UX +
plan-gate-checking in the API.

## Repo split

When does the codebase split? Recommended cuts:

| Trigger                              | Action                                                                  |
| ------------------------------------ | ----------------------------------------------------------------------- |
| Start of Phase 3 (hosted backend)    | Create `flow-bof-saas/` repo. SaaS API + frontend live there.           |
| Start of Phase 6 (agent packaging)   | Create `flow-bof-agent/` repo. The agent source moves out of `flow-bof-automation/`. |
| Anything before Phase 3              | Stay in this repo. We don't want premature multi-repo overhead.         |

What stays in this (`flow-bof-automation`) repo permanently:
- The Python automation (`src/recorded_flow.py`, `src/flow_tiles.py`,
  `src/flow_automation.py`, `src/manifest_workflow.py`).
- The `ai/providers/` modules.
- The Kalodata importer.
- `docker-compose.yml` + `docker/cdp-proxy.conf`.
- The Streamlit UI (kept as a fallback / dev tool even after SaaS exists).

Eventually:
- The Python automation modules get extracted into a `flow-automation-core`
  package both the local Streamlit app AND the future agent depend on.
  We don't need this until Phase 6 — until then we git-vendor.

## Risk register

| Risk                                                | Mitigation                                                                 |
| --------------------------------------------------- | -------------------------------------------------------------------------- |
| Flow Labs UI changes mid-migration                  | Phase 1 keeps the existing tests + locator helpers; selector updates land in one place. |
| Cloud→agent latency makes BOF runs feel sluggish    | Phase 5 starts with polling; upgrade to WebSocket if measured latency is >2 s. |
| Signing certificates expire / get pulled            | Establish renewal calendar in Phase 6; have a self-update fallback path.   |
| Agents on flaky home internet drop mid-job          | Phase 5 already requires resume-or-fail; Phase 6 adds local checkpointing. |
| Stripe + tax surprises                              | Treat Phase 7 as a separate project plan; not bundled with the migration.  |
| User data migration (current `data/batches/` → SaaS)| Phase 4 ships a "Import from local Docker app" button that reads `data/batches/` and POSTs to the SaaS API. |
| Two parallel UIs (Streamlit + Next.js) drift        | Pick a date (probably between Phase 6 and 7) to freeze Streamlit. After that it's read-only for archived users; new features only land in Next.js. |

## Recommended first implementation step

**Start with Phase 1.** Specifically:

1. Create `src/agent_api.py` with these five function stubs (raising
   `NotImplementedError` for now):

   ```python
   def handle_health_check() -> AgentResult: ...
   def handle_generate_flow_images(params: dict) -> Iterator[AgentEvent]: ...
   def handle_scan_favorited_images() -> Iterator[AgentEvent]: ...
   def handle_generate_flow_videos_from_favorites(params: dict) -> Iterator[AgentEvent]: ...
   def handle_check_flow_connection(params: dict) -> Iterator[AgentEvent]: ...
   ```

2. Define `AgentResult` and `AgentEvent` as `TypedDict`s matching the
   contract in [`JOB_PROTOCOL.md`](JOB_PROTOCOL.md).

3. Wire one real handler — `handle_health_check` is the easiest —
   end-to-end. It calls `src.health.run_all_checks()` and shapes
   the result.

4. Add a unit test that imports the handler and asserts the schema.

5. Add a single command to `main.py`:

   ```
   python main.py --agent-job health_check
   ```

   which round-trips a job through the handler and prints the result.
   This becomes the proving ground for every later handler.

After that step lands, the migration becomes a series of small "lift
one handler at a time" PRs — none of which touch the Docker app, the
manifest format, or the existing CLI surface.

## What should stay in this repo forever

- `src/recorded_flow.py` — the recorded UI flow against Flow Labs.
- `src/flow_tiles.py` — the tile scanner.
- `src/flow_automation.py` — the Playwright session helpers.
- `src/manifest_workflow.py` — the per-job orchestration (eventually
  imported by the agent rather than driven by the Streamlit UI).
- `ai/providers/` — the model abstractions.
- `src/kalodata_importer.py` — the Excel parser.
- `docker/cdp-proxy.conf` — the proxy config.

## What should eventually split out

| Eventually a separate repo                  | Today's path                          |
| ------------------------------------------- | ------------------------------------- |
| `flow-bof-saas` (Next.js + FastAPI)         | (doesn't exist yet)                   |
| `flow-bof-agent` (PyInstaller bundle)       | `agent/` skeleton in this repo        |
| `flow-automation-core` (shared Python lib)  | `src/` of this repo                   |

## What should be archived but not deleted

When we cross over to the SaaS-driven world (after Phase 7):

- `streamlit_app.py` — keep as a debugging UI for engineering. Mark
  in the README as "local-only debug tool; the SaaS dashboard is the
  product".
- The Streamlit-driven CLI commands — keep. They're useful tests.
- The `setup.ps1` / `setup.sh` lifecycle scripts — keep for the small
  set of testers who prefer the all-local mode.

The bar for deletion is **"this hasn't been used in 6 months"** — not
"this isn't in the production path".
