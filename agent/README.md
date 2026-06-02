# agent/ — placeholder

This folder is where the eventual local agent will live until it gets
packaged into its own repo + installer (see Phase 6 of
[../docs/MIGRATION_PLAN_TO_SAAS.md](../docs/MIGRATION_PLAN_TO_SAAS.md)).

**Nothing here is wired into the running app.** The Docker-based local
alpha continues to be the source of truth. The Python automation
modules in `src/` and `ai/` are what the agent will eventually import.

## When to start filling this in

Phase 2 of the migration plan: when we expose the automation as a local
HTTP service the Streamlit UI can call.

Phase 6: when we package the agent as a standalone `agent.exe` /
`agent.dmg`, this folder probably becomes its own repo
(`flow-bof-agent/`).

## Design docs

- [../docs/LOCAL_AGENT_ARCHITECTURE.md](../docs/LOCAL_AGENT_ARCHITECTURE.md) — agent responsibilities, Chrome control, comms.
- [../docs/JOB_PROTOCOL.md](../docs/JOB_PROTOCOL.md) — wire format with the SaaS.
- [../docs/MIGRATION_PLAN_TO_SAAS.md](../docs/MIGRATION_PLAN_TO_SAAS.md) — phased rollout.

## What the agent will be

- Python program (initially), eventually a PyInstaller bundle or
  Tauri-wrapped binary.
- Local FastAPI service on `127.0.0.1:9444` for local Streamlit calls
  (Phase 2).
- Persistent WebSocket or polling client to SaaS (Phase 5).
- Reuses **unchanged** Python modules from the parent repo:
  `src/recorded_flow.py`, `src/flow_tiles.py`, `src/flow_automation.py`,
  `src/manifest_workflow.py`, `src/health.py`, `src/video_state.py`.

## What the agent won't be

- A UI surface beyond a system tray + token-pairing form.
- A store of business data (products, prompts, batches). All of that
  lives in the SaaS.
- A holder of Google or TikTok credentials. Those stay in the user's
  Chrome profile, which the agent talks to via CDP — never reads
  directly.
- An AI consumer. Prompts arrive pre-baked from the SaaS.

## Do not put here

- Anything the Docker compose stack needs today. The current alpha
  keeps working without touching this folder.
- A second copy of `src/` modules. The agent eventually imports them
  (later: vendors them); we don't fork the code path.
