# Local agent HTTP API

A tiny FastAPI wrapper around `src.agent_api.handle_agent_job`. Same
job envelopes the CLI accepts; HTTP transport instead of subprocess.

Status: **Phase-2 alpha** — useful for the upcoming SaaS dashboard
prototype and for desktop wrappers. **Not** something to expose on the
public internet without setting `AGENT_API_TOKEN`.

## Starting the server

### Local Docker (recommended)

The compose file ships an opt-in `agent` profile so the server doesn't
start by default. Bring it up with:

```bash
docker compose --profile agent up -d agent
```

The port is published on `127.0.0.1:9444` only — Docker Desktop's
default port-binding rules apply, so the agent isn't reachable from
your LAN.

To stop:

```bash
docker compose --profile agent down
```

### Ad-hoc Docker run

If you don't want to commit the profile state, just run:

```bash
docker compose run --rm -p 127.0.0.1:9444:9444 app python main.py --agent-server
```

The container exits when you Ctrl-C.

### Direct CLI

```bash
python main.py --agent-server
```

Boots `uvicorn src.agent_server:app --host 127.0.0.1 --port 9444`.

### Configuration knobs

| Env var           | Default     | Purpose                                                     |
| ----------------- | ----------- | ----------------------------------------------------------- |
| `AGENT_API_HOST`  | `127.0.0.1` | Bind interface. Set to `0.0.0.0` only with a token set.     |
| `AGENT_API_PORT`  | `9444`      | TCP port.                                                   |
| `AGENT_API_TOKEN` | *(unset)*   | If set, every request needs `Authorization: Bearer <token>`. |

## Endpoints

### `GET /`

Identity probe. Cheap, unauthenticated equivalent of `GET /jobs/types`
when you don't yet know the protocol version.

```bash
curl -s http://127.0.0.1:9444/
```

```json
{
  "service":          "flow-bof-local-agent",
  "agent_version":    "0.5.0-alpha",
  "protocol_version": "0.1",
  "auth_required":    false,
  "endpoints": ["GET  /", "GET  /health", "GET  /jobs/types",
                "POST /jobs/run", "POST /jobs/run-stream"]
}
```

### `GET /health`

Runs the `health_check` agent job. Always returns HTTP 200; the
response body is the same envelope the CLI emits, with status
`succeeded`/`failed` encoded inside.

```bash
curl -s http://127.0.0.1:9444/health | jq .
```

### `GET /jobs/types`

Lists the agent job types the agent's code currently knows about.
Useful for SaaS-side feature-gating ("the user's agent doesn't yet
support `tiktok_draft`").

```bash
curl -s http://127.0.0.1:9444/jobs/types | jq .
```

```json
{
  "protocol_version": "0.1",
  "agent_version":    "0.5.0-alpha",
  "job_types": [
    "check_flow_connection",
    "generate_flow_images",
    "generate_flow_videos_from_favorites",
    "health_check",
    "scan_favorited_images"
  ]
}
```

### `POST /jobs/run`

Submit a full job envelope. Returns the final envelope when the job
completes. The HTTP connection stays open for the entire run — this
can be many minutes for image / video batches. If you want live
updates, use `/jobs/run-stream`.

```bash
curl -s -X POST http://127.0.0.1:9444/jobs/run \
     -H 'Content-Type: application/json' \
     -d @tmp_job.json | jq .
```

### `POST /jobs/run-stream`

Submit a full job envelope and stream progress as **NDJSON**
(`application/x-ndjson` — one JSON object per line). Each line is
either a progress event (`event_type: "progress"`) or the final
wrapped envelope (`event_type: "result"`). The connection closes
after the last line.

```bash
curl -N -X POST http://127.0.0.1:9444/jobs/run-stream \
     -H 'Content-Type: application/json' \
     -d @tmp_job.json
```

Sample output:

```
{"protocol_version":"0.1","job_id":"v-001","job_type":"generate_flow_videos_from_favorites","event_type":"progress","stage":"scanning_favorites","message":"Scanning favorited images...","current":null,"total":null,"details":{}}
{"protocol_version":"0.1","job_id":"v-001","job_type":"generate_flow_videos_from_favorites","event_type":"progress","stage":"favorites_found","message":"Found 3 favorited image(s); 3 to process, 0 already submitted.","current":null,"total":null,"details":{"favorited_images_found":3,"skipped_already_submitted":0,"to_process":3}}
{"protocol_version":"0.1","job_id":"v-001","job_type":"generate_flow_videos_from_favorites","event_type":"progress","stage":"processing_tile","message":"Submitting video 1 of 3...","current":1,"total":3,"details":{"media_id":"...","tile_id":"...","edit_id":"..."}}
...
{"event_type":"result","envelope":{"protocol_version":"0.1","job_id":"v-001","job_type":"generate_flow_videos_from_favorites","status":"succeeded","result":{...},"error":null}}
```

## Auth

Auth is opt-in. The server starts with no auth unless `AGENT_API_TOKEN`
is set in the environment. If set, all endpoints require:

```
Authorization: Bearer <AGENT_API_TOKEN>
```

Examples:

```bash
export AGENT_API_TOKEN="$(openssl rand -hex 32)"
docker compose --profile agent up -d agent

curl -s -H "Authorization: Bearer $AGENT_API_TOKEN" \
        http://127.0.0.1:9444/health
```

A missing or wrong token returns HTTP 401.

The token is never logged. The server uses a constant-time compare so
the response timing doesn't reveal token length or prefix.

## Security

- **The server binds to `127.0.0.1` by default.** Don't change this
  without also setting `AGENT_API_TOKEN`.
- The agent can drive the user's logged-in Chrome. Anyone who can hit
  the port (even read-only) can scan the user's Flow grid; anyone who
  can POST can submit images/videos. Treat the port like a control
  surface, not a public API.
- No CORS configured. Browsers can't call the agent from an
  HTTPS origin against `http://127.0.0.1` without a tunnel; that's
  intentional for the alpha.
- TLS is not provided. If/when the SaaS dashboard connects, it'll do
  so through a local desktop wrapper that bridges localhost ↔ TLS
  tunnel ↔ SaaS WebSocket, not via direct browser→agent traffic.

## Example payloads

### health_check

```json
{
  "protocol_version": "0.1",
  "job_id": "h1",
  "job_type": "health_check",
  "payload": {}
}
```

### scan_favorited_images

```json
{
  "protocol_version": "0.1",
  "job_id": "s1",
  "job_type": "scan_favorited_images",
  "payload": {
    "limit": 100,
    "include_non_favorites": false,
    "include_videos": false
  }
}
```

### check_flow_connection

```json
{
  "protocol_version": "0.1",
  "job_id": "c1",
  "job_type": "check_flow_connection",
  "payload": {}
}
```

### generate_flow_videos_from_favorites

```json
{
  "protocol_version": "0.1",
  "job_id": "v1",
  "job_type": "generate_flow_videos_from_favorites",
  "payload": {
    "limit": 30,
    "include_already_submitted": false,
    "blanket_video_prompt": null
  }
}
```

### generate_flow_images

```json
{
  "protocol_version": "0.1",
  "job_id": "i1",
  "job_type": "generate_flow_images",
  "payload": {
    "items": [
      {
        "item_id": "01",
        "product_name": "Example Product",
        "reference_image_path": "inputs/reference_images/01_primary.jpg",
        "image_prompt": "Use the uploaded reference image only..."
      }
    ],
    "limit": 30,
    "wait_mode": "submit_only",
    "automation_mode": "fast"
  }
}
```

## Common errors

| Symptom | Likely cause |
| --- | --- |
| `connection refused` | The agent server isn't running. `docker compose --profile agent up -d agent` or `python main.py --agent-server`. |
| HTTP 401 | `AGENT_API_TOKEN` is set; you didn't send the matching Bearer token. |
| `result.status == "failed"` with `code = "CHROME_NOT_REACHABLE"` | Host Chrome is closed. Start it via `./start.sh` / `start.ps1`. |
| `result.status == "failed"` with `code = "FLOW_PAGE_NOT_FOUND"` | Chrome is up, but no Flow tab is open. Open `https://labs.google/flow` in the debug Chrome window. |
| `result.status == "failed"` with `code = "AGENT_DEPENDENCY_MISSING"` | You're hitting `/health` outside the Docker container and Playwright isn't installed on the host. Run the server inside Docker. |
| The stream just stops without a `result` line | The agent process crashed mid-job. Inspect `docker compose logs agent` (or stderr of `python main.py --agent-server`). The auto-restart `restart: unless-stopped` will bring it back. |

## What this replaces

Nothing yet. The existing CLI/Streamlit paths keep working unchanged.
The HTTP API is **additive**: it gives the future SaaS dashboard a
clean way to call the agent without inventing a new transport. Phase-3
plans how a hosted backend will speak to this surface.

The local Streamlit UI doesn't talk to the agent server yet — it still
subprocesses `python main.py …` via `run_cli` like it always has.
Migrating Streamlit's `run_cli` to an HTTP client is its own task on
the Phase-2 punch list (separate PR; not in scope here).
