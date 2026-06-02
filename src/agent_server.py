"""Phase-2 local agent HTTP API.

A thin FastAPI wrapper around :func:`src.agent_api.handle_agent_job`.
The point of this server is to let future callers (a hosted SaaS
dashboard via a tunnel, a desktop wrapper, a CLI client, etc.) drive
the agent without shelling out to ``python main.py --agent-job ...``.

Hard constraints (these are the boundaries between "local agent" and
"hosted SaaS" — keep them):

* Binds to ``127.0.0.1`` by default. Set ``AGENT_API_HOST=0.0.0.0``
  only when you know what you're doing (e.g. inside a Docker network
  bridge where the port isn't published to the host's public iface).
* No persistence beyond what the underlying handlers already do —
  this module holds no state of its own.
* No new business logic. Every endpoint either:
    - forwards to ``handle_agent_job``, or
    - returns metadata derived from ``known_job_types()``.
* No `--reload`, no auto-discovery. Production posture from day one.

Endpoints
---------
``GET  /``                 — server identity (version, protocol).
``GET  /health``           — runs the ``health_check`` agent job.
``GET  /jobs/types``       — list of registered job types.
``POST /jobs/run``         — submit a job envelope; returns the final result.
``POST /jobs/run-stream``  — submit a job envelope; streams JSONL progress.

Auth
----
If ``AGENT_API_TOKEN`` is set in the agent's environment, every request
must send ``Authorization: Bearer <token>``. If unset, the server
accepts every request — fine for ``127.0.0.1``, but if you bind a
public interface you SHOULD set the token.

Streaming format (``/jobs/run-stream``)
--------------------------------------
``application/x-ndjson`` — one JSON object per line. Each line is
either:

* a progress event (``"event_type": "progress"``), or
* the final job envelope (``"event_type": "result"``).

The connection closes after the final line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
from typing import Any, Optional

try:
    from fastapi import (
        Body,
        Depends,
        FastAPI,
        Header,
        HTTPException,
        status,
    )
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "agent_server requires fastapi + uvicorn. Install with:\n"
        "  pip install fastapi uvicorn\n"
        f"Underlying error: {exc}"
    )

from .agent_api import (
    APP_VERSION,
    PROTOCOL_VERSION,
    handle_agent_job,
    known_job_types,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9444


def _agent_token() -> str:
    """Read the optional bearer token from env. Empty = no auth required."""
    return (os.environ.get("AGENT_API_TOKEN") or "").strip()


def _require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency. No-op when AGENT_API_TOKEN is unset; otherwise
    enforces ``Authorization: Bearer <token>`` exactly. Never logs the
    token itself.
    """
    expected = _agent_token()
    if not expected:
        return  # no auth configured
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
        )
    presented = authorization[len("Bearer "):].strip()
    # Constant-time compare to avoid timing leaks. Length-mismatched
    # tokens still fall through to the False branch but won't reveal
    # the expected length via timing.
    import hmac
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bearer token",
        )


# ---------------------------------------------------------------------------
# Server-side logger
# ---------------------------------------------------------------------------

logger = logging.getLogger("agent.server")
if not logger.handlers:
    # Mirror uvicorn's default format so logs interleave cleanly.
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(h)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Flow BOF Local Agent",
    version=APP_VERSION,
    description=(
        "Local-only HTTP wrapper around the agent job interface. "
        "See docs/LOCAL_AGENT_HTTP_API.md."
    ),
    # Routes that need protection use Depends(_require_token); applying
    # the dependency globally would block the unauthenticated /health
    # probe a Phase-3 SaaS would want.
)


@app.get("/")
def root() -> dict:
    return {
        "service":           "flow-bof-local-agent",
        "agent_version":     APP_VERSION,
        "protocol_version":  PROTOCOL_VERSION,
        "auth_required":     bool(_agent_token()),
        "endpoints": [
            "GET  /",
            "GET  /health",
            "GET  /jobs/types",
            "POST /jobs/run",
            "POST /jobs/run-stream",
        ],
    }


@app.get("/health")
def health(_=Depends(_require_token)) -> JSONResponse:
    """Run the underlying health_check agent job. Returns the same
    envelope the CLI ``--agent-job health_check`` produces, so SaaS
    code can keep one parser for both transports."""
    job = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id":           "http-health",
        "job_type":         "health_check",
        "payload":          {},
    }
    result = handle_agent_job(job, logger=logger)
    # /health always 200s — the *envelope* says whether the agent and
    # its environment are happy. A SaaS dashboard wants to render the
    # red/green indicators itself, not have HTTP error codes telling
    # it the agent is half-broken.
    return JSONResponse(content=result, status_code=200)


@app.get("/jobs/types")
def job_types(_=Depends(_require_token)) -> dict:
    """Returns the list of registered job_type strings the agent
    understands. Useful for SaaS-side feature gating ("agent v0.5
    doesn't support tiktok_draft yet")."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "agent_version":    APP_VERSION,
        "job_types":        known_job_types(),
    }


@app.post("/jobs/run")
def jobs_run(
    job: dict = Body(...),
    _=Depends(_require_token),
) -> JSONResponse:
    """Submit a full job envelope and get the final result back.

    No progress streaming — the connection stays open for the whole
    run, which can be many minutes for image/video batches. Use
    ``/jobs/run-stream`` when you want live updates.
    """
    if not isinstance(job, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request body must be a JSON object",
        )
    result = handle_agent_job(job, logger=logger)
    # Always 200 with the structured envelope. Whether the JOB
    # succeeded or failed is encoded in result.status. This lets a
    # SaaS treat 5xx as "the agent broke" and the envelope's
    # status field as "the job's outcome".
    return JSONResponse(content=result, status_code=200)


@app.post("/jobs/run-stream")
def jobs_run_stream(
    job: dict = Body(...),
    _=Depends(_require_token),
) -> StreamingResponse:
    """Submit a job and stream NDJSON progress events.

    Wire format: ``application/x-ndjson``. Each line is a JSON object.

    * Intermediate lines are progress events (``event_type: "progress"``).
    * The last line is the final job envelope wrapped as
      ``{event_type: "result", "envelope": {...}}``. The client knows the
      stream is over because the connection closes after that line.

    The handler runs in a background thread so the FastAPI event loop
    can drain the progress queue and write each event to the response
    body as soon as the handler emits it. A ``threading.Event`` plus
    a sentinel value on the queue signals the loop to wrap up.
    """
    if not isinstance(job, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request body must be a JSON object",
        )

    # Thread-safe handoff between the synchronous handler (worker
    # thread) and the async generator (event loop thread).
    event_q: "queue.Queue[Any]" = queue.Queue()
    SENTINEL = object()

    def _progress_callback(evt: dict) -> None:
        # The handler is on the worker thread; just put-and-go.
        try:
            event_q.put(evt)
        except Exception:  # noqa: BLE001
            # Queue failures are non-fatal — drop the event.
            pass

    final_result_box: dict = {}

    def _run_handler() -> None:
        try:
            final_result_box["envelope"] = handle_agent_job(
                job,
                logger=logger,
                progress_callback=_progress_callback,
            )
        except Exception as exc:  # noqa: BLE001
            # handle_agent_job is supposed to never raise, but just in
            # case: build a failure envelope so the stream still
            # closes cleanly.
            final_result_box["envelope"] = {
                "protocol_version": PROTOCOL_VERSION,
                "job_id":           job.get("job_id", ""),
                "job_type":         job.get("job_type", ""),
                "status":           "failed",
                "result":           None,
                "error": {
                    "code":    "AGENT_SERVER_PANIC",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                },
            }
        finally:
            event_q.put(SENTINEL)

    worker = threading.Thread(target=_run_handler, daemon=True)

    async def _stream():
        worker.start()
        loop = asyncio.get_running_loop()
        while True:
            # Drain the cross-thread queue without blocking the event
            # loop. queue.get is sync; we offload to a default executor.
            evt = await loop.run_in_executor(None, event_q.get)
            if evt is SENTINEL:
                break
            try:
                yield (json.dumps(evt) + "\n").encode("utf-8")
            except Exception:  # noqa: BLE001
                # One bad event must not kill the stream.
                continue
        # Final envelope as the last line, wrapped so the client can
        # distinguish it from progress events by event_type alone.
        envelope = final_result_box.get("envelope") or {
            "protocol_version": PROTOCOL_VERSION,
            "job_id":           job.get("job_id", ""),
            "job_type":         job.get("job_type", ""),
            "status":           "failed",
            "result":           None,
            "error": {
                "code":    "AGENT_SERVER_NO_RESULT",
                "message": "handler did not return an envelope",
                "details": {},
            },
        }
        yield (
            json.dumps({"event_type": "result", "envelope": envelope})
            + "\n"
        ).encode("utf-8")

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
    )


# ---------------------------------------------------------------------------
# Entry point used by main.py --agent-server
# ---------------------------------------------------------------------------


def run() -> int:
    """Boot uvicorn. Reads host/port from env.

    Env knobs:
      AGENT_API_HOST   default 127.0.0.1
      AGENT_API_PORT   default 9444
      AGENT_API_TOKEN  optional Bearer token (see _require_token)
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "agent_server requires uvicorn. Install with:\n"
            "  pip install uvicorn\n"
            f"Underlying error: {exc}"
        )

    host = (os.environ.get("AGENT_API_HOST") or DEFAULT_HOST).strip()
    try:
        port = int(os.environ.get("AGENT_API_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    if host not in ("127.0.0.1", "localhost") and not _agent_token():
        logger.warning(
            "AGENT_API_HOST=%s but AGENT_API_TOKEN is unset. "
            "Anyone who can reach this port can drive the agent. "
            "Set AGENT_API_TOKEN before binding a non-loopback iface.",
            host,
        )

    logger.info(
        "Starting agent HTTP API on http://%s:%d "
        "(auth_required=%s, agent_version=%s, protocol_version=%s)",
        host, port, bool(_agent_token()), APP_VERSION, PROTOCOL_VERSION,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
