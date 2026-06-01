# Docker setup (Phase 1)

Container runs the Python CLI; **Chrome stays on the Windows host**. The container drives Chrome over CDP via an in-network nginx proxy because Chrome rejects the `Host: host.docker.internal:9222` header that Docker's network would otherwise send (see "Why an nginx CDP proxy?" below).

```
┌──────────────────────────────────────────────────────────────────┐
│  Windows host                                                    │
│                                                                  │
│   Real Chrome  --remote-debugging-port=9222                      │
│   (signed into Google, on labs.google/flow)                      │
│        ▲                                                         │
│        │   host.docker.internal:9222                             │
│        │   (Host header rewritten to 127.0.0.1:9222)             │
│        │                                                         │
│   ┌────┴───────────────────────────────────┐                     │
│   │  [cdp-proxy]  nginx:alpine, port 9333  │                     │
│   └────────────────────────────────────────┘                     │
│        ▲                                                         │
│        │   http://cdp-proxy:9333                                 │
│        │                                                         │
│   ┌────┴───────────────────────────────────────────┐             │
│   │  [app]  Python CLI                             │             │
│   │    /app  (project bind-mounted from host)      │             │
│   │    python main.py …                            │             │
│   └────────────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

Chrome is never inside Docker. Playwright in the container calls `playwright.chromium.connect_over_cdp("http://cdp-proxy:9333")` and the proxy forwards to your real Chrome with a rewritten `Host` header.

## 1. Install Docker Desktop

[Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/). Start it once — it must be running whenever you use the container.

Confirm with:

```powershell
docker info
```

If `docker info` errors, Docker Desktop isn't up.

## 2. Start the Chrome debug profile

Close every existing Chrome window first (the debug port only opens when nothing else is using the same profile). Then:

```powershell
scripts\start_chrome_debug.ps1
```

This launches Chrome with a dedicated profile in `%USERPROFILE%\chrome-flow-automation` so your normal browsing isn't affected. Leave the window open while you work.

## 3. Log into Flow Labs

In that new Chrome window:

1. Sign in to your Google account.
2. Navigate to https://labs.google/flow.

You only need to do this once per profile — the sign-in persists in `chrome-flow-automation`.

## 4. Verify Chrome CDP from the host

```powershell
Invoke-WebRequest http://localhost:9222/json/version -UseBasicParsing
```

A 200 response with a JSON body like `{"Browser":"Chrome/148.x.y.z", ...}` means the debug port is open and reachable.

`scripts\start_app.ps1` runs the same check and prints the daily-flow commands.

## 5. Verify the container can reach Chrome

```powershell
# Build the image once (re-run when requirements.txt changes).
docker compose build

# Smoke-test the proxy reaches Chrome and rewrites the Host header.
docker compose run --rm app python -c "import urllib.request; print(urllib.request.urlopen('http://cdp-proxy:9333/json/version').read().decode()[:500])"
# Expected: a JSON document with "Browser":"Chrome/...", and the
# webSocketDebuggerUrl rewritten to ws://cdp-proxy:9333/devtools/...

# Now the app-level check.
docker compose run --rm app python main.py --check-browser
```

Expected: Chrome version, open pages, and `Flow Labs reachable: yes`. If something errors:

- The proxy can't be reached → the cdp-proxy container didn't start. `docker compose ps` should show it Up.
- The proxy returns 502 → the host's Chrome isn't listening on 9222. Re-run `scripts\start_chrome_debug.ps1`.
- The proxy returns 500 → see "Why an nginx CDP proxy?" below.
- The app times out on the WebSocket → `sub_filter` didn't rewrite the `webSocketDebuggerUrl`. Confirm the JSON from the smoke test above contains `ws://cdp-proxy:9333` (not `ws://localhost:9222`).
- **The app gets `403 Forbidden / Rejected an incoming WebSocket connection from the http://127.0.0.1:9222 origin`** → Chrome ≥ 116 enforces an Origin allow-list on the WebSocket handshake. Chrome was launched without `--remote-allow-origins=*`. Almost always this means Windows handed your launch off to an already-running Chrome process that ignored the new flags. **Close every Chrome window** (`Get-Process chrome | Stop-Process`) and re-run `scripts\start_chrome_debug.ps1`.

## 6. Run the daily pipeline

Once `--check-browser` is green, the daily commands are the same as the host-Python flow — just wrapped in `docker compose run --rm app …`:

```powershell
# Validate the manifest (read-only).
docker compose run --rm app python main.py --validate-manifest

# Start a fresh batch from inputs/prompt_manifest.md (backs up old CSV).
docker compose run --rm app python main.py --load-manifest --fresh

# Quick queue check.
docker compose run --rm app python main.py --list-status

# Generate images for up to 30 pending rows.
docker compose run --rm app python main.py --generate-images --limit 30

# (Heart your favorites visually in the real Chrome window.)

# Approve hearted rows.
docker compose run --rm app python main.py --sync-favorites

# Animate approved rows.
docker compose run --rm app python main.py --generate-videos --limit 30
```

Outputs (logs, screenshots, the CSV) write to your host's `outputs/` and `inputs/` because both are bind-mounted into the container.

## Stopping

`docker compose run --rm` removes each container as it exits, so nothing accumulates. If something hangs:

```powershell
scripts\stop_app.ps1
```

## How the container is wired

[docker-compose.yml](../docker-compose.yml) hardcodes two environment variables for the app container:

```yaml
environment:
  BROWSER_MODE: remote_debugging
  CHROME_CDP_URL: http://cdp-proxy:9333
```

So even if your host's `.env` has `CHROME_CDP_URL=http://127.0.0.1:9222` for local-Python use, the container always points at the in-network proxy. The rest of the env vars (timeouts, FLOW_LABS_URL, etc.) fall through from your host `.env` via Docker Compose's variable substitution — see [.env.docker.example](../.env.docker.example) for the user-tunable surface.

## Why an nginx CDP proxy?

Chrome's `--remote-debugging-port` hardens the DevTools endpoint by rejecting any HTTP request whose `Host` header isn't `127.0.0.1`, `localhost`, or a bare IP. From the Windows shell this is invisible — `Invoke-WebRequest http://127.0.0.1:9222/json/version` works because the Host header is `127.0.0.1:9222`.

From inside the container, however, `host.docker.internal` is the DNS name Docker uses to reach the host gateway. Python's `urllib`/Playwright correctly include it in the `Host` header (`host.docker.internal:9222`), and Chrome answers **HTTP 500** — usually with `Host header is specified and is not an IP address or localhost`. Playwright's `connect_over_cdp` then fails before it can open a websocket.

[docker/cdp-proxy.conf](../docker/cdp-proxy.conf) sits on the docker network on port 9333. It:

1. Forwards `/*` requests to `host.docker.internal:9222`.
2. Sets `Host: 127.0.0.1:9222` and `Origin: http://127.0.0.1:9222` on the upstream request — Chrome's allow-list is satisfied, so it returns the real JSON.
3. Handles the WebSocket upgrade (`proxy_http_version 1.1`, `Upgrade`, `Connection: "upgrade"`) so the long-lived CDP session works.
4. Uses `sub_filter` to rewrite the `webSocketDebuggerUrl` that Chrome embeds in `/json/version` and `/json` — it replaces `ws://localhost:9222` / `ws://127.0.0.1:9222` with `ws://cdp-proxy:9333`. Without this, Playwright would receive a websocket URL pointing at the *container's* loopback, which never reaches Chrome.

End result: the app container speaks plain CDP to the proxy as if it were Chrome itself, and the proxy launders the headers/URLs so the host Chrome accepts the traffic.

### Smoke tests

```powershell
# 1. Does the proxy reach Chrome and rewrite headers?
docker compose run --rm app python -c "import urllib.request; print(urllib.request.urlopen('http://cdp-proxy:9333/json/version').read().decode()[:500])"

# 2. Does Playwright successfully attach?
docker compose run --rm app python main.py --check-browser
```

Both must succeed before the daily pipeline will work.

## Why not run Chrome in the container?

- Google blocks sign-in in fully sandboxed/Playwright-launched browsers, so we'd be back to the original problem we solved by attaching to real Chrome.
- Chrome inside Docker on Windows would need WSL2 and X-forwarding to be interactive — significant complexity for no gain.
- The current flow lets you visually heart images in the same Chrome window where they were generated, which is exactly what `--sync-favorites` expects.

## When to rebuild the image

```powershell
docker compose build
```

Only needed when [requirements.txt](../requirements.txt) changes or you edit the [Dockerfile](../Dockerfile). Code edits don't need a rebuild — the project root is bind-mounted into the container at `/app`.
