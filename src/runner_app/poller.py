"""Thin wrapper around src.runner_poller.RunnerPoller.

The existing RunnerPoller reads SAAS_BASE_URL / RUNNER_TOKEN / etc.
from os.environ. The standalone runner reads them from
runner_config.json instead. To avoid forking RunnerPoller we just
inject the config values into os.environ before constructing it.

We also force CHROME_CDP_URL + FLOW_LABS_URL onto the loaded
src.config.Settings — those control how perform_recorded_flow et al.
talk to Chrome. The packaged exe always talks directly to the
local Chrome on 127.0.0.1:<port>; the cdp-proxy container is a dev-
only convenience that doesn't ship in the exe.
"""

from __future__ import annotations

import logging
import os
import signal
from typing import Any

from .config import RunnerConfig
from .chrome import cdp_url


logger = logging.getLogger("runner_app.poller")


def _set_env_for_poller(cfg: RunnerConfig) -> None:
    """Stuff the runner config into os.environ so the existing
    RunnerPoller + src.config can see it. We `setdefault` rather
    than overwrite so anything the user explicitly set on the
    command line keeps precedence."""
    os.environ.setdefault("SAAS_BASE_URL", cfg.saas_base_url)
    os.environ.setdefault("RUNNER_TOKEN", cfg.runner_token)
    os.environ.setdefault(
        "RUNNER_POLL_INTERVAL_SECONDS", str(cfg.poll_interval_seconds)
    )
    os.environ.setdefault(
        "RUNNER_HEALTH_INTERVAL_SECONDS", str(cfg.health_interval_seconds)
    )
    os.environ.setdefault(
        "RUNNER_HTTP_TIMEOUT_SECONDS", str(cfg.http_timeout_seconds)
    )

    # These two are what the agent_api job handlers read at job time.
    # Setting them BEFORE the first `from src.config import
    # load_settings` import (which the existing poller does lazily
    # inside handle_agent_job) is what guarantees the runner talks
    # to local Chrome on 127.0.0.1, not to the dev cdp-proxy
    # container.
    os.environ["CHROME_CDP_URL"] = cdp_url(cfg.chrome_debug_port)
    if cfg.flow_url:
        os.environ["FLOW_LABS_URL"] = cfg.flow_url


def run(cfg: RunnerConfig) -> int:
    """Run the poller until SIGINT / SIGTERM. Returns the same exit
    code RunnerPoller.run_forever returns."""
    _set_env_for_poller(cfg)

    # Late import: src.runner_poller imports src.agent_api which
    # pulls in heavy modules. Deferring keeps `--diagnose` / `--reset
    # -config` fast.
    from src.runner_poller import (
        RunnerPoller,
        _install_signal_handlers,  # type: ignore[attr-defined]
    )

    _install_signal_handlers()

    logger.info("Flow BOF Runner starting")
    logger.info("  SaaS    : %s", cfg.saas_base_url)
    logger.info("  Token   : %s", cfg.token_masked())
    logger.info("  Chrome  : %s", cdp_url(cfg.chrome_debug_port))
    logger.info("Polling for jobs every %.0fs. Ctrl+C to stop.",
                cfg.poll_interval_seconds)

    poller = RunnerPoller(
        saas_base_url=cfg.saas_base_url,
        runner_token=cfg.runner_token,
        poll_interval_seconds=cfg.poll_interval_seconds,
        health_interval_seconds=cfg.health_interval_seconds,
        http_timeout_seconds=cfg.http_timeout_seconds,
    )
    return poller.run_forever()
