# Streamlit ↔ local agent HTTP mode (Phase 2b)

By default the Streamlit UI subprocesses `python main.py …` for every
button that drives Flow. If you've booted the local agent HTTP API
([docs/LOCAL_AGENT_HTTP_API.md](LOCAL_AGENT_HTTP_API.md)), you can flip
the UI to route agent-supported actions through that API instead. Same
buttons, cleaner progress, and a stepping stone to the SaaS dashboard.

**Status:** opt-in. The CLI path is the default and always works.

## What's wired

Today the UI routes these actions over HTTP when execution mode is `http`:

| UI action / button | CLI flag | Agent job |
| --- | --- | --- |
| Check Browser (Advanced/Logs) | `--check-browser` | `health_check` |
| Scan Favorites *(list-only path)* | `--list-unmatched-favorites` | `scan_favorited_images` |
| Generate Videos from Favorited Images | `--generate-videos --limit N [--include-already-submitted]` | `generate_flow_videos_from_favorites` |

Actions that mutate the CSV / manifest / batch state — load-manifest,
validate-manifest, sync-favorites, generate-images-from-CSV — always
stay on the CLI path, even in HTTP mode. Those will migrate one at a
time as their agent handlers grow CSV-aware semantics.

## How to start the agent

```bash
docker compose --profile agent up -d agent
```

Verify it's up:

```bash
curl -s http://127.0.0.1:9444/health | jq .
```

You should see `"status": "succeeded"` with `chrome_reachable` /
`flow_reachable` flags inside `result`.

## How to switch the UI to HTTP mode

1. Open the Streamlit UI: <http://localhost:8080>.
2. Sidebar → **Setup**.
3. Scroll to **Execution mode (advanced)**.
4. Click **Test Agent API**. You should see the green
   "Connected" banner. If not, see Troubleshooting below.
5. Set **Execution mode** to **Local Agent HTTP**.
6. (Optional) Paste a value into **Agent API token** if the agent was
   started with `AGENT_API_TOKEN` set in its environment.
7. Click **💾 Save execution mode**.

The change takes effect immediately. The next time you click a
migrated button (Generate Videos, etc.) the UI runs it through the
agent's `/jobs/run-stream` endpoint with NDJSON progress events
instead of subprocessing `main.py`.

## What the user sees in HTTP mode

Same friendly progress card as CLI mode, just sourced from real
structured progress events instead of regex-matched log lines:

```
Generating videos from favorited images (via local agent) — done in 184.3s.
Elapsed: 184.3s · Submitted: 5 · Failed: 0 · Skipped (already submitted): 0
▸ Show technical details
```

The "Show technical details" expander now contains:

- the final JSON envelope, and
- the raw NDJSON event stream

instead of stdout/stderr from a subprocess. Both forms preserve the
exact same `last_command_summary` and friends in `session_state`, so
the Advanced/Logs page and Recent activity expander keep working.

## How to switch back to CLI mode

Same flow — Setup → Execution mode → **Local CLI** → Save. No agent
service needs to be running for CLI mode; if you want to stop the
agent container after switching:

```bash
docker compose --profile agent down
```

(The default `docker compose up -d` won't touch the agent service
either way because it lives under the `agent` profile.)

## Configuration knobs

These propagate from **Setup → Execution mode** into `os.environ` so
both the UI and any subprocesses see them:

| Setting (UI label) | Env var | Default |
| --- | --- | --- |
| Execution mode | `AGENT_EXECUTION_MODE` | `cli` |
| Agent base URL | `AGENT_BASE_URL` | `http://127.0.0.1:9444` |
| Agent API token | `AGENT_API_TOKEN` | *(unset)* |

The token is stored in `data/secrets.local.json` (same file as your AI
API keys). It's never logged.

## Troubleshooting

### "Agent API: Not connected"

The status chip on the Setup page polls `/health` once every ~30s. If
it's red:

1. Is the agent service running?
   ```bash
   docker compose --profile agent ps
   ```
2. If not: `docker compose --profile agent up -d agent`.
3. Wait ~10s, then click **Test Agent API** in the UI to refresh.

If the service is up but still unreachable, check:

```bash
docker compose --profile agent logs agent | tail -30
```

The most common cause is the agent waiting for Chrome — make sure
`./start.ps1` / `./start.sh` ran first so the host Chrome is open
with `--remote-debugging-port=9222`.

### "Local Agent API is not running. Start it with: …"

That message comes from `_run_via_agent_http` when an HTTP request
fails. It means the UI was in HTTP mode but the agent didn't respond.
Two options:

1. Start the agent: `docker compose --profile agent up -d agent`.
2. Or switch back to CLI mode in Setup.

### Token-related 401s

If you started the agent with `AGENT_API_TOKEN=<something>` but
forgot to paste the same value into the UI's Agent API token field,
every request 401s. Either set the matching token in the UI or
restart the agent without the token:

```bash
unset AGENT_API_TOKEN
docker compose --profile agent up -d --force-recreate agent
```

### Going back to the legacy behavior

There's nothing destructive about HTTP mode. The CLI path is still
there, untouched. Setup → Execution mode → **Local CLI** → Save flips
the switch back. Subsequent actions subprocess `main.py` exactly as
before.

## Why not migrate every action at once?

Two reasons:

1. Some actions read or write `inputs/products.csv` (the manifest path,
   the favorite-sync path). Those are still CSV-driven and don't have
   1:1 agent jobs yet. Routing them through HTTP would either lose
   that state or require parallel implementations. Migrating one at a
   time, with thorough comparison, is safer.
2. The CLI path is well-tested by alpha users. Keeping it as the
   default while the HTTP wrapper bakes lets us roll back instantly if
   we hit a bug in the agent server, the streaming layer, or the UI's
   NDJSON parser — without breaking any flows.

When the SaaS dashboard lands (Phase 4) it'll be HTTP-only and the CLI
path retires from the alpha-tester experience. Until then both
transports remain supported.
