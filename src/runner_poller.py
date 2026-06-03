"""Connected-runner polling client.

This module is the runner-side counterpart of
``flow-bof-saas/src/app/api/runner/*``. A long-lived loop that:

  1. Dials out to the SaaS at SAAS_BASE_URL with a Bearer
     RUNNER_TOKEN, hits POST /api/runner/health to advertise this
     runner.
  2. Hits POST /api/runner/jobs/next to pick up the oldest queued
     job whose job_type this runner can run.
  3. Runs the job via the same :func:`src.agent_api.handle_agent_job`
     entrypoint the CLI and the local HTTP API both use. Per-event
     progress callbacks POST to /api/runner/jobs/:id/events.
  4. POSTs the final envelope to /api/runner/jobs/:id/complete
     (success or "envelope-failed"). Hard exceptions outside the
     handler land on /api/runner/jobs/:id/fail.

Things this module deliberately does NOT do:

  * Never sends Google / TikTok cookies anywhere — those live in the
    user's Chrome profile and stay there.
  * Never forwards API keys (OpenAI etc.) to the SaaS. The SaaS owns
    those; the runner only receives finished prompts inside job
    payloads.
  * Never trusts the SaaS to tell it which Python module to import.
    Job dispatch goes through the same handler table the CLI uses;
    job_types the runner doesn't know about become failed envelopes.

Env vars (see docs/CONNECTED_RUNNER.md):

  SAAS_BASE_URL                   required, e.g. https://app.autobof.xyz
  RUNNER_TOKEN                    required, runner_<base64url>
  RUNNER_POLL_INTERVAL_SECONDS    default 5
  RUNNER_HEALTH_INTERVAL_SECONDS  default 60
  RUNNER_HTTP_TIMEOUT_SECONDS     default 30
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import sys
import time
from typing import Any

import httpx

from .agent_api import APP_VERSION, handle_agent_job, known_job_types


logger = logging.getLogger("runner_poller")


# Single sentinel the SIGINT / SIGTERM handler flips. The main loop
# checks it between sleeps + before every HTTP call so Ctrl-C exits
# at a clean boundary (no in-flight job is interrupted).
_should_stop = False


def _install_signal_handlers() -> None:
    def _stop(signum: int, _frame: Any) -> None:
        global _should_stop
        _should_stop = True
        logger.info("received signal %s; will stop after current iteration.", signum)
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)


def _require_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(
            f"missing required environment variable: {name}. "
            f"See docs/CONNECTED_RUNNER.md."
        )
    return v


def _base_url() -> str:
    return _require_env("SAAS_BASE_URL").rstrip("/")


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


class RunnerPoller:
    """Long-lived polling client. Constructed once per process."""

    def __init__(
        self,
        saas_base_url: str,
        runner_token: str,
        poll_interval_seconds: float = 5.0,
        health_interval_seconds: float = 60.0,
        http_timeout_seconds: float = 30.0,
    ) -> None:
        self.saas_base_url = saas_base_url.rstrip("/")
        self.runner_token = runner_token
        self.poll_interval = max(1.0, float(poll_interval_seconds))
        self.health_interval = max(5.0, float(health_interval_seconds))
        self.http_timeout = max(5.0, float(http_timeout_seconds))
        self._client = httpx.Client(
            base_url=self.saas_base_url,
            headers=_auth_headers(self.runner_token),
            timeout=self.http_timeout,
        )
        self._last_health = 0.0
        self._capabilities = known_job_types()

    # ----- low-level HTTP -------------------------------------------------

    def _post(self, path: str, json: dict | None = None) -> httpx.Response:
        """POST with structured logging on transport failure. Raises
        only on truly fatal conditions; HTTP 4xx/5xx are returned so
        the caller can decide. Never logs the Authorization header."""
        try:
            resp = self._client.post(path, json=json or {})
        except httpx.HTTPError as exc:
            logger.warning("HTTP error on POST %s: %s", path, exc)
            raise
        if resp.status_code >= 500:
            logger.warning(
                "POST %s → %s (server error). Body: %s",
                path, resp.status_code, resp.text[:200],
            )
        elif resp.status_code == 401:
            logger.error(
                "POST %s → 401 unauthorized. Check RUNNER_TOKEN.",
                path,
            )
        return resp

    # ----- protocol calls -------------------------------------------------

    def health(self) -> bool:
        """One health check-in. Returns True if the SaaS accepted us."""
        body = {
            "runnerVersion": APP_VERSION,
            "platform": (
                f"{platform.python_implementation()} {platform.python_version()} "
                f"on {platform.system()} {platform.release()}"
            ),
            "capabilities": self._capabilities,
        }
        try:
            resp = self._post("/api/runner/health", body)
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        self._last_health = time.monotonic()
        try:
            data = resp.json()
            agent_id = data.get("agentId", "?")
            logger.info("health ok → agent %s; server time %s",
                        agent_id, data.get("serverTime"))
        except ValueError:
            pass
        return True

    def next_job(self) -> dict | None:
        """Ask for the oldest queued job we can run. None if idle."""
        try:
            resp = self._post(
                "/api/runner/jobs/next",
                {"capabilities": self._capabilities},
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        job = data.get("job")
        if not job:
            return None
        # The job dict already conforms to the envelope shape
        # handle_agent_job expects (protocol_version / job_id /
        # job_type / payload). We carry through an extra `id` field
        # the SaaS uses for the /:id/events + /:id/complete routes.
        return job

    def post_event(self, job_id: str, event: dict) -> None:
        """Persist one progress event. Never raises — progress is
        informational; a missed event must not crash the loop."""
        # Map the agent_api emitter's shape onto the /events body
        # the SaaS expects (it accepts both shapes; this is the
        # superset for clarity).
        body = {
            "event_type": event.get("event_type") or "progress",
            "stage":      event.get("stage"),
            "message":    event.get("message"),
            "current":    event.get("current"),
            "total":      event.get("total"),
            "details":    event.get("details"),
        }
        try:
            self._post(f"/api/runner/jobs/{job_id}/events", body)
        except httpx.HTTPError as exc:
            logger.warning("event POST failed for job %s: %s", job_id, exc)

    def complete(self, job_id: str, envelope: dict) -> None:
        body = {"envelope": envelope}
        try:
            resp = self._post(f"/api/runner/jobs/{job_id}/complete", body)
            if resp.status_code != 200:
                logger.warning(
                    "complete POST for %s returned %s: %s",
                    job_id, resp.status_code, resp.text[:200],
                )
        except httpx.HTTPError as exc:
            logger.error("complete POST failed for job %s: %s", job_id, exc)

    def fail(self, job_id: str, error: dict) -> None:
        body = {"error": error}
        try:
            self._post(f"/api/runner/jobs/{job_id}/fail", body)
        except httpx.HTTPError as exc:
            logger.error("fail POST failed for job %s: %s", job_id, exc)

    # ----- main loop -------------------------------------------------------

    def run_forever(self) -> int:
        """Block until SIGINT/SIGTERM. Returns 0 on clean shutdown."""
        logger.info(
            "runner poller starting → %s (poll=%.1fs, health=%.1fs)",
            self.saas_base_url, self.poll_interval, self.health_interval,
        )
        logger.info("capabilities: %s", ", ".join(self._capabilities))

        # First /health right away so the dashboard flips green
        # immediately instead of after the first idle poll.
        if not self.health():
            logger.warning(
                "initial /health POST failed. Check SAAS_BASE_URL + "
                "RUNNER_TOKEN. Will keep retrying."
            )

        while not _should_stop:
            now = time.monotonic()
            if now - self._last_health >= self.health_interval:
                self.health()

            job = self.next_job()
            if job is None:
                self._sleep(self.poll_interval)
                continue

            saas_job_id = job.get("id") or job.get("job_id") or ""
            job_type = job.get("job_type") or "?"
            logger.info("claimed job %s (%s)", saas_job_id, job_type)

            # Per-job progress callback wired to /events. The agent
            # API's emitter swallows callback exceptions, so a network
            # blip here can't kill the running job.
            def _on_event(evt: dict, _jid: str = saas_job_id) -> None:
                self.post_event(_jid, evt)

            try:
                envelope = handle_agent_job(
                    job, logger=logger, progress_callback=_on_event,
                )
                self.complete(saas_job_id, envelope)
                status = envelope.get("status")
                logger.info("job %s → %s", saas_job_id, status)
            except Exception as exc:  # noqa: BLE001
                # handle_agent_job is documented to never raise, but
                # belt-and-suspenders: any escape becomes an explicit
                # /fail POST so the SaaS doesn't see the job stuck at
                # "running" forever.
                logger.exception("runner crashed on job %s", saas_job_id)
                self.fail(saas_job_id, {
                    "code":    "RUNNER_CRASH",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                })

            # Drop straight back into the loop — we don't sleep
            # between consecutive jobs so a backlog drains quickly.

        logger.info("runner poller exiting cleanly.")
        self._client.close()
        return 0

    def _sleep(self, seconds: float) -> None:
        # Chunked sleep so Ctrl-C lands within a second even if the
        # poll interval is long.
        end = time.monotonic() + seconds
        while not _should_stop and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))


def run() -> int:
    """CLI entrypoint for `python main.py --runner-poll`."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    _install_signal_handlers()

    saas_base_url = _base_url()
    runner_token  = _require_env("RUNNER_TOKEN")
    poll = float(os.environ.get("RUNNER_POLL_INTERVAL_SECONDS") or 5)
    hb   = float(os.environ.get("RUNNER_HEALTH_INTERVAL_SECONDS") or 60)
    to_  = float(os.environ.get("RUNNER_HTTP_TIMEOUT_SECONDS") or 30)

    poller = RunnerPoller(
        saas_base_url=saas_base_url,
        runner_token=runner_token,
        poll_interval_seconds=poll,
        health_interval_seconds=hb,
        http_timeout_seconds=to_,
    )
    return poller.run_forever()
