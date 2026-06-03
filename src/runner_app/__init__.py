"""Standalone Flow BOF Runner — a Docker-free, end-user-friendly
wrapper around the connected-runner polling client.

The flow:

  runner_app.main()
    └─ load or prompt for config (paths.py + config.py)
    └─ launch dedicated debug Chrome + open Flow (chrome.py)
    └─ set CHROME_CDP_URL / FLOW_LABS_URL env so src.config picks them up
    └─ start the existing RunnerPoller (poller.py)

This package is intended to be PyInstaller-packaged into a single
FlowBOFRunner.exe so an end user never sees Docker / Python / Git.
The original Docker / `python main.py --runner-poll` paths still work
unchanged for developers.
"""

__all__ = []
