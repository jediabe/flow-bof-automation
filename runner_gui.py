"""Flow BOF Runner — lightweight status-window GUI.

A thin Tkinter shell around the existing console runner. Subprocess-
launches `runner_app.py --run --no-pause` (or its packaged exe
equivalent), pipes its stderr/stdout into a 50-line tail panel, and
exposes the actions a user actually presses day-to-day:

  - Start runner          (spawn subprocess)
  - Stop runner           (SIGTERM the subprocess, wait ~2s)
  - Open Flow browser     (subprocess --open-browser, returns)
  - Inspect Flow UI       (subprocess --inspect-flow, returns)
  - Re-enter SaaS / token (subprocess --setup, blocks until done)
  - Run diagnostics       (subprocess --diagnose, captures output)

Design choices:

  - **Tkinter, stdlib only.** No third-party UI dep — no extra wheel
    for PyInstaller to bundle (~3-5MB delta on the exe). Looks native
    enough with ttk widgets; not flashy, but visually pleasing in the
    user's sense (clean rows, monospace log, clear status).

  - **GUI wraps the existing console runner, doesn't replace it.**
    The console runner (`runner_app.py` interactive menu + CLI flags)
    keeps working exactly as before. The GUI subprocess-launches the
    SAME exe / script with specific flags, so any selector-fix /
    inspector / agent_api work the user does later applies to both
    surfaces with zero extra wiring.

  - **Event-driven log capture.** A background thread reads
    subprocess.stderr line-by-line and pushes lines into a
    thread-safe Queue, then calls `root.event_generate("<<RunnerLog>>")`
    so the Tk main thread wakes ONLY when there's data. No periodic
    polling — the idle GUI is essentially zero-CPU.

  - **Subprocess management.** SIGTERM-then-wait pattern. The runner
    poller's _sleep cap is 2s (see runner_poller.py), so Stop completes
    in ~2s on a graceful path. SIGKILL only as last resort after 5s.

Run modes:

  - Dev:        `python runner_gui.py`
  - Packaged:   `FlowBOFRunner.exe --gui`  (added to runner_app.py)

The packaged exe in either mode discovers itself via `sys.executable`
+ `getattr(sys, "frozen", False)` so the GUI subprocess-launches the
same binary it lives inside. One spec, one exe, dual-mode.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import ttk
from typing import Optional


# ---------------------------------------------------------------------
# Update check (GitHub Releases API)
# ---------------------------------------------------------------------
# Public read endpoint; no auth needed. Rate-limited to 60/hr for
# unauth callers — fine for a manual button. The check NEVER auto-
# downloads or replaces the running binary — it just compares the
# embedded APP_VERSION to the latest release tag and pops a modal so
# the user can open the release page in their browser and update
# manually.
GITHUB_RELEASES_API = (
    "https://api.github.com/repos/jediabe/flow-bof-automation/releases/latest"
)
GITHUB_RELEASES_PAGE = (
    "https://github.com/jediabe/flow-bof-automation/releases"
)


def _runner_version() -> str:
    """Return the embedded APP_VERSION so we can compare against the
    GitHub release tag. Late-import keeps the import cost out of the
    GUI startup path."""
    try:
        from src.agent_api import APP_VERSION  # type: ignore
        return str(APP_VERSION)
    except Exception:  # noqa: BLE001
        return "unknown"


def _platform_asset_ext() -> str | None:
    """Filename suffix the auto-updater expects for this platform's
    release asset. None on Linux (no packaged distribution yet)."""
    if sys.platform == "darwin":
        return ".dmg"
    if sys.platform.startswith("win"):
        return ".exe"
    return None


def _pick_asset_for_platform(assets: list) -> dict | None:
    """Find the right binary in a GitHub release's `assets[]` list.

    Match strategy is conservative: filename extension only. This
    means our release process has freedom over the asset filename
    (e.g. `FlowBOFRunner-mac-alpha.dmg` vs `FlowBOFRunner.dmg`) as
    long as the extension is right.

    Returns the first matching asset dict (size + browser_download_url
    + name) or None when no suitable binary is in the release.
    """
    ext = _platform_asset_ext()
    if ext is None:
        return None
    ext_lower = ext.lower()
    for a in assets:
        name = (a.get("name") or "").lower()
        if name.endswith(ext_lower):
            return a
    return None


# ---------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------

_BG          = "#1e1e1e"
_PANEL_BG    = "#252526"
_TEXT        = "#e6e6e6"
_TEXT_DIM    = "#9c9c9c"
_ACCENT      = "#3794ff"
_OK          = "#3ec97c"
_WARN        = "#e0a23d"
_BAD         = "#f14c4c"
_BTN_BG      = "#3a3d41"
_BTN_BG_HOV  = "#4a4d51"
_BTN_BG_DIS  = "#2a2c2e"
_BTN_TEXT    = "#ffffff"
_LOG_BG      = "#1a1a1a"
_LOG_TEXT    = "#d4d4d4"

_WIN_TITLE   = "Flow BOF Runner"
_WIN_W       = 720
_WIN_H       = 540


# ---------------------------------------------------------------------
# Subprocess discovery — find the exe / script to launch
# ---------------------------------------------------------------------

def _runner_invocation() -> list[str]:
    """Return the argv prefix to launch the console runner.

    When the GUI itself is the packaged exe (`sys.frozen`), reusing
    `sys.executable` runs the same binary — we just pass different
    flags to bypass the menu. When the GUI is being run from source
    via `python runner_gui.py`, we point at `runner_app.py` next to
    this file using the current Python interpreter.
    """
    if getattr(sys, "frozen", False):
        # Packaged: same exe, different flags.
        return [sys.executable]
    runner_script = Path(__file__).parent / "runner_app.py"
    return [sys.executable, str(runner_script)]


def _platform_popen_kwargs() -> dict:
    """OS-specific Popen kwargs for clean subprocess management.

    Windows: CREATE_NO_WINDOW hides the cmd flash + lets us SIGTERM
             cleanly via taskkill-equivalent. CREATE_NEW_PROCESS_GROUP
             so Ctrl-C in the GUI process doesn't propagate.
    Unix:    start_new_session puts the child in its own process
             group, so killpg works without taking out the GUI.
    """
    kwargs: dict = {
        "stdin":  subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,  # merge — runner logs to stderr,
                                       # mixing keeps order intact.
        "bufsize": 1,                  # line-buffered
        "text":  True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


# ---------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------

class RunnerGUI:
    """Top-level Tk window. Owns the subprocess, the log queue, and
    the status state machine."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: Optional[subprocess.Popen[str]] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.log_queue: "queue.Queue[str]" = queue.Queue(maxsize=2000)
        # Cross-thread channel for update-check / download callbacks.
        # Workers put (fn, args) tuples; the <<UpdateEvent>> handler
        # drains and invokes on the Tk main thread. Using a queue +
        # event_generate instead of `root.after(0, ...)` because the
        # latter silently drops callbacks from non-main threads on
        # macOS Tahoe + Tk 9.0 (root.after is not actually documented
        # thread-safe; event_generate is).
        self.update_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=128)
        self.log_lines: list[str] = []
        self._max_log_lines = 200  # bound the on-screen tail
        self._current_status = "stopped"  # stopped | running | working

        self._build_ui()
        self._bind_events()
        self._set_status("stopped")

    # ----- UI ----------------------------------------------------------

    def _build_ui(self) -> None:
        r = self.root
        r.title(_WIN_TITLE)
        r.geometry(f"{_WIN_W}x{_WIN_H}")
        r.minsize(560, 420)
        r.configure(bg=_BG)

        # ttk style — dark theme without third-party deps.
        style = ttk.Style(r)
        try:
            style.theme_use("clam")  # the most themeable built-in
        except tk.TclError:
            pass
        style.configure(
            "TFrame", background=_BG,
        )
        style.configure(
            "Panel.TFrame", background=_PANEL_BG,
        )
        style.configure(
            "TLabel",
            background=_BG, foreground=_TEXT,
            font=("Segoe UI", 10) if sys.platform.startswith("win") else ("Helvetica", 11),
        )
        style.configure(
            "Dim.TLabel",
            background=_BG, foreground=_TEXT_DIM,
        )
        style.configure(
            "Header.TLabel",
            background=_BG, foreground=_TEXT,
            font=("Segoe UI", 11, "bold") if sys.platform.startswith("win") else ("Helvetica", 12, "bold"),
        )
        style.configure(
            "TButton",
            background=_BTN_BG, foreground=_BTN_TEXT,
            borderwidth=0, focusthickness=0,
            padding=(12, 6),
        )
        style.map(
            "TButton",
            background=[
                ("active", _BTN_BG_HOV),
                ("disabled", _BTN_BG_DIS),
            ],
            foreground=[("disabled", _TEXT_DIM)],
        )
        style.configure(
            "Accent.TButton",
            background=_ACCENT, foreground="#ffffff",
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", "#5aa5ff"),
                ("disabled", _BTN_BG_DIS),
            ],
            foreground=[("disabled", _TEXT_DIM)],
        )

        # Header — status badge + SaaS info.
        header = ttk.Frame(r, padding=(16, 14, 16, 8))
        header.pack(fill=tk.X)

        self.status_label = ttk.Label(
            header, text="● Stopped", style="Header.TLabel",
        )
        self.status_label.pack(side=tk.LEFT)

        self.status_detail = ttk.Label(
            header, text="", style="Dim.TLabel",
        )
        self.status_detail.pack(side=tk.LEFT, padx=(12, 0))

        # Quit on the far right of the header.
        quit_btn = ttk.Button(header, text="Quit", command=self._on_quit)
        quit_btn.pack(side=tk.RIGHT)

        # Buttons row.
        buttons = ttk.Frame(r, padding=(16, 4, 16, 12))
        buttons.pack(fill=tk.X)

        self.btn_start = ttk.Button(
            buttons, text="Start runner",
            style="Accent.TButton", command=self._on_start,
        )
        self.btn_start.pack(side=tk.LEFT)

        self.btn_stop = ttk.Button(
            buttons, text="Stop", command=self._on_stop,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_open = ttk.Button(
            buttons, text="Open Flow", command=self._on_open_flow,
        )
        self.btn_open.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_inspect = ttk.Button(
            buttons, text="Inspect Flow", command=self._on_inspect,
        )
        self.btn_inspect.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_setup = ttk.Button(
            buttons, text="Setup…", command=self._on_setup,
        )
        self.btn_setup.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_update = ttk.Button(
            buttons, text="Check for updates", command=self._on_check_updates,
        )
        self.btn_update.pack(side=tk.RIGHT)

        # Log panel — monospace, dark background, scrolled.
        log_frame = ttk.Frame(r, padding=(16, 0, 16, 16))
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_header = ttk.Label(log_frame, text="Recent log", style="Dim.TLabel")
        log_header.pack(anchor=tk.W, pady=(0, 4))

        # Choose a monospace font safe on every platform.
        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=10 if sys.platform.startswith("win") else 11)

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.NONE,
            bg=_LOG_BG, fg=_LOG_TEXT,
            insertbackground=_TEXT,
            font=mono,
            relief=tk.FLAT,
            borderwidth=0,
            padx=10, pady=8,
            state=tk.DISABLED,
        )
        sb_y = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tag colours so warnings / errors stand out without colourising
        # the whole line at log-source time.
        self.log_text.tag_configure("warn", foreground=_WARN)
        self.log_text.tag_configure("error", foreground=_BAD)
        self.log_text.tag_configure("ok", foreground=_OK)

        # Close button → graceful shutdown.
        r.protocol("WM_DELETE_WINDOW", self._on_quit)

    # ----- Event bindings ---------------------------------------------

    def _bind_events(self) -> None:
        # The reader thread fires this from outside the main thread;
        # Tk handles serialisation. We drain the queue when we wake.
        self.root.bind("<<RunnerLog>>", self._drain_log_queue)
        # Same pattern for update-check / download callbacks.
        self.root.bind("<<UpdateEvent>>", self._drain_update_queue)
        # Periodic process-status poll. 1s is fine — only matters for
        # detecting an unexpected subprocess exit; live logs come
        # through the event channel.
        self.root.after(1000, self._tick_process_status)

    # ----- Subprocess lifecycle ---------------------------------------

    def _on_start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self._append_log("[gui] runner already running\n", tag="warn")
            return
        argv = _runner_invocation() + ["--run", "--no-pause"]
        self._append_log(f"[gui] starting: {' '.join(argv)}\n")
        try:
            self.proc = subprocess.Popen(argv, **_platform_popen_kwargs())
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[gui] failed to start: {exc}\n", tag="error")
            self.proc = None
            return
        self._start_reader_thread()
        self._set_status("running")

    def _on_stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self._append_log("[gui] runner is not running\n", tag="warn")
            return
        self._append_log("[gui] stopping runner (SIGTERM)…\n")
        self._signal_subprocess_terminate()
        # Wait up to 5s; if it doesn't exit, escalate to SIGKILL.
        try:
            self.proc.wait(timeout=5.0)
            self._append_log("[gui] runner stopped cleanly.\n", tag="ok")
        except subprocess.TimeoutExpired:
            self._append_log(
                "[gui] runner didn't exit within 5s; force-killing.\n",
                tag="warn",
            )
            try:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"[gui] kill failed: {exc}\n", tag="error")
        self.proc = None
        self._set_status("stopped")

    def _signal_subprocess_terminate(self) -> None:
        """Send a clean termination signal that the runner's existing
        SIGINT/SIGTERM handlers will catch."""
        if self.proc is None:
            return
        try:
            if sys.platform.startswith("win"):
                # CTRL_BREAK_EVENT is the only signal that reaches a
                # subprocess started with CREATE_NEW_PROCESS_GROUP on
                # Windows. The runner's signal.SIGTERM handler also
                # receives it via Python's signal module shim.
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                # killpg targets the whole process group (the runner
                # plus any Playwright helpers it spawned).
                os.killpg(self.proc.pid, signal.SIGTERM)
        except Exception as exc:  # noqa: BLE001
            self._append_log(
                f"[gui] terminate signal failed: {exc}; trying .terminate()\n",
                tag="warn",
            )
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    # ----- One-shot actions -------------------------------------------

    def _on_open_flow(self) -> None:
        self._run_oneshot(["--open-browser"], "open Flow browser")

    def _on_inspect(self) -> None:
        self._run_oneshot(["--inspect-flow"], "inspect Flow UI")

    def _on_check_updates(self) -> None:
        """Fetch latest GitHub release tag, compare to embedded
        APP_VERSION, offer the release page in the user's browser
        if a newer version is published.

        Runs the HTTP fetch on a background thread so the Tk
        mainloop never blocks. Modal dialogs are posted back to
        the main thread via `after(0, ...)` so Tk only touches
        widgets from its own thread.
        """
        self.btn_update.state(["disabled"])
        self._append_log("[gui] checking for updates...\n")
        threading.Thread(
            target=self._update_check_worker,
            name="update-check",
            daemon=True,
        ).start()

    def _update_check_worker(self) -> None:
        current = _runner_version().lstrip("v").strip()
        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"FlowBOFRunner/{current}",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            self._post(self._update_check_failed, f"GitHub returned HTTP {exc.code}")
            return
        except urllib.error.URLError as exc:
            self._post(self._update_check_failed, f"Network error: {exc.reason}")
            return
        except Exception as exc:  # noqa: BLE001
            self._post(self._update_check_failed, f"{type(exc).__name__}: {exc}")
            return

        latest_tag = (payload.get("tag_name") or "").strip()
        release_url = (payload.get("html_url") or GITHUB_RELEASES_PAGE).strip()
        assets = payload.get("assets") or []
        latest_normalized = latest_tag.lstrip("v").strip()
        if not latest_normalized:
            self._post(
                self._update_check_failed,
                "Latest release has no tag_name field.",
            )
            return

        self._post(
            self._update_check_finished,
            current, latest_normalized, release_url, assets,
        )

    def _post(self, fn, *args) -> None:
        """Schedule `fn(*args)` to run on the Tk main thread.

        Uses a thread-safe queue + `event_generate` to marshal the
        callback to the main thread. Originally this was a one-liner
        `self.root.after(0, lambda: fn(*args))`, which appeared to
        work on Linux + Windows + older macOS but silently drops
        callbacks on macOS Tahoe with Tk 9.0 (root.after is NOT
        documented thread-safe; event_generate is). The same pattern
        already used for runner stdout streaming.
        """
        try:
            self.update_queue.put_nowait((fn, args))
        except queue.Full:
            # Drop oldest so a runaway producer can't deadlock UI
            # updates. Should never trigger in practice (the queue
            # holds 128 items and producers are infrequent).
            try:
                self.update_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.update_queue.put_nowait((fn, args))
            except queue.Full:
                return
        try:
            self.root.event_generate("<<UpdateEvent>>", when="tail")
        except (tk.TclError, RuntimeError):
            # Tk has been torn down (window closing) — nothing to
            # post to anymore.
            return

    def _drain_update_queue(self, _event: tk.Event | None = None) -> None:
        """Main-thread handler for <<UpdateEvent>>. Drains the queue
        and invokes each callback. Exceptions are logged + traced so
        a single bad callback doesn't take down later ones."""
        import traceback
        while True:
            try:
                fn, args = self.update_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                self._append_log(
                    f"[gui] update callback failed: "
                    f"{type(exc).__name__}: {exc}\n",
                    tag="error",
                )
                # Also print full traceback to stderr so it shows in
                # the launching Terminal during dev (`python runner_gui.py`).
                traceback.print_exc()

    def _update_check_failed(self, reason: str) -> None:
        self.btn_update.state(["!disabled"])
        self._append_log(f"[gui] update check failed: {reason}\n", tag="warn")
        messagebox.showwarning(
            "Update check failed",
            f"Could not check for updates.\n\n{reason}\n\n"
            f"You can browse releases manually:\n{GITHUB_RELEASES_PAGE}",
        )

    def _update_check_finished(
        self, current: str, latest: str, release_url: str, assets: list,
    ) -> None:
        self.btn_update.state(["!disabled"])
        self._append_log(
            f"[gui] update check: local v{current} / latest v{latest}\n",
        )
        if current == latest:
            messagebox.showinfo(
                "Up to date",
                f"You're running v{current}, which matches the latest "
                f"published release.",
            )
            return

        # Version strings differ. Pick the right asset (.dmg on Mac,
        # .exe on Windows) and offer a one-click download.
        asset = _pick_asset_for_platform(assets)
        if asset is None:
            # No matching binary in the release. Fall back to the
            # browser flow so the user can still grab whatever the
            # release does have (e.g. a source-only tag).
            self._append_log(
                f"[gui] no .{_platform_asset_ext() or '?'} asset in release "
                f"v{latest} — falling back to release page\n", tag="warn",
            )
            if messagebox.askyesno(
                "Update available",
                f"Your runner:    v{current}\n"
                f"Latest release: v{latest}\n\n"
                f"No installer for this platform in the release. "
                f"Open the release page in your browser?",
                default=messagebox.YES,
            ):
                try:
                    webbrowser.open(release_url)
                except Exception:  # noqa: BLE001
                    messagebox.showinfo(
                        "Open this URL",
                        f"Copy this URL:\n\n{release_url}",
                    )
            return

        size_mb = (asset.get("size") or 0) / 1024 / 1024
        name = asset.get("name") or "FlowBOFRunner update"
        download_url = asset.get("browser_download_url") or ""
        if not download_url:
            self._update_check_failed("Release asset has no download URL.")
            return

        # Modal: tell the user what we'd download + offer it.
        if not messagebox.askyesno(
            "Update available",
            f"Your runner:    v{current}\n"
            f"Latest release: v{latest}\n\n"
            f"Download:  {name}\n"
            f"Size:      {size_mb:.1f} MB\n\n"
            f"Saves to your Downloads folder and opens automatically "
            f"when the download finishes. Your token + config survive "
            f"the upgrade.",
            default=messagebox.YES,
        ):
            return

        self._start_update_download(name, download_url, latest, release_url)

    # ----- Download flow ----------------------------------------------

    def _start_update_download(
        self, name: str, url: str, version: str, release_url: str,
    ) -> None:
        """Open the progress dialog and kick off the worker thread."""
        dest_dir = Path.home() / "Downloads"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            dest_dir = Path.home()
        dest = dest_dir / name

        # Cancel signal that the worker thread polls between chunks.
        self._update_cancel = threading.Event()

        # Progress dialog
        dlg = tk.Toplevel(self.root)
        dlg.title("Downloading update")
        dlg.configure(bg=_BG)
        dlg.geometry("420x180")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: self._update_cancel.set())

        ttk.Label(
            dlg, text=f"Downloading {name}",
            style="Header.TLabel", padding=(16, 14, 16, 4),
        ).pack(anchor=tk.W)
        ttk.Label(
            dlg, text=f"→ {dest}",
            style="Dim.TLabel", padding=(16, 0, 16, 8),
        ).pack(anchor=tk.W)

        progress = ttk.Progressbar(
            dlg, mode="determinate", length=380, maximum=100,
        )
        progress.pack(padx=16, pady=4)
        self._update_progress_bar = progress

        status = ttk.Label(
            dlg, text="0.0 / ? MB",
            style="Dim.TLabel", padding=(16, 4, 16, 0),
        )
        status.pack(anchor=tk.W)
        self._update_progress_status = status

        btn_row = ttk.Frame(dlg)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=10)
        ttk.Button(
            btn_row, text="Cancel",
            command=lambda: self._update_cancel.set(),
        ).pack(side=tk.RIGHT)

        self._update_progress_dialog = dlg
        self._append_log(f"[gui] downloading {name}...\n")

        threading.Thread(
            target=self._update_download_worker,
            args=(url, dest, version, release_url),
            name="update-download",
            daemon=True,
        ).start()

    def _update_download_worker(
        self, url: str, dest: Path, version: str, release_url: str,
    ) -> None:
        """HTTP-stream the asset to disk, reporting progress.

        Posts progress events back to the Tk thread via queue +
        `<<UpdateProgress>>`. Cancel-aware: checks the
        `self._update_cancel` Event between chunks, deletes the
        partial file on cancel.
        """
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"FlowBOFRunner/{_runner_version()}"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(dest, "wb") as fh:
                    while True:
                        if self._update_cancel.is_set():
                            fh.close()
                            try:
                                dest.unlink()
                            except Exception:  # noqa: BLE001
                                pass
                            self._post(self._update_download_cancelled)
                            return
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        self._post(self._update_download_progress, done, total)
        except Exception as exc:  # noqa: BLE001
            try:
                if dest.exists():
                    dest.unlink()
            except Exception:  # noqa: BLE001
                pass
            self._post(self._update_download_failed, f"{type(exc).__name__}: {exc}")
            return

        self._post(self._update_download_done, dest, version, release_url)

    def _update_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = min(100, (done * 100) // total)
            self._update_progress_bar.configure(value=pct)
            self._update_progress_status.configure(
                text=f"{done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB ({pct}%)",
            )
        else:
            # Indeterminate — no Content-Length header. Spin the bar.
            self._update_progress_bar.configure(mode="indeterminate")
            self._update_progress_bar.start(50)
            self._update_progress_status.configure(
                text=f"{done / 1024 / 1024:.1f} MB downloaded",
            )

    def _update_download_cancelled(self) -> None:
        self._close_progress_dialog()
        self._append_log("[gui] download cancelled\n", tag="warn")

    def _update_download_failed(self, reason: str) -> None:
        self._close_progress_dialog()
        self._append_log(f"[gui] download failed: {reason}\n", tag="error")
        messagebox.showerror(
            "Download failed",
            f"Could not download the update.\n\n{reason}\n\n"
            f"You can grab it manually from the release page.",
        )

    def _update_download_done(
        self, dest: Path, version: str, release_url: str,
    ) -> None:
        self._close_progress_dialog()
        size_mb = dest.stat().st_size / 1024 / 1024
        self._append_log(
            f"[gui] downloaded {dest.name} ({size_mb:.1f} MB) → {dest}\n",
            tag="ok",
        )
        # Auto-open according to platform.
        try:
            if sys.platform == "darwin":
                # `open` on a .dmg mounts it + opens the Finder window
                # showing the .app — user drags to Applications.
                subprocess.run(["open", str(dest)], check=False)
                finish_msg = (
                    f"Downloaded v{version} to:\n  {dest}\n\n"
                    f"Finder is mounting the disk image now. Drag "
                    f"FlowBOFRunner.app into Applications (replacing "
                    f"the old one), then relaunch from Applications."
                )
            elif sys.platform.startswith("win"):
                # Reveal the new .exe in Explorer so the user can
                # move it over the running one. The running .exe is
                # file-locked; we can't replace it from inside.
                try:
                    subprocess.run(
                        ["explorer", "/select,", str(dest)], check=False,
                    )
                except Exception:  # noqa: BLE001
                    os.startfile(str(dest.parent))  # type: ignore[attr-defined]
                finish_msg = (
                    f"Downloaded v{version} to:\n  {dest}\n\n"
                    f"Explorer is showing the new file. Close this app, "
                    f"then move the new FlowBOFRunner.exe over your "
                    f"existing one and double-click to launch."
                )
            else:
                finish_msg = (
                    f"Downloaded v{version} to:\n  {dest}\n\n"
                    f"Replace your existing runner with this file and "
                    f"relaunch."
                )
        except Exception as exc:  # noqa: BLE001
            self._append_log(
                f"[gui] auto-open failed: {exc} (file is at {dest})\n",
                tag="warn",
            )
            finish_msg = (
                f"Downloaded to:\n  {dest}\n\n"
                f"(Auto-open failed — find the file manually.)"
            )

        messagebox.showinfo("Update downloaded", finish_msg)
        # Offer release notes as a follow-up — useful when the user
        # wants to see what changed before installing.
        if messagebox.askyesno(
            "Release notes",
            "Open the release notes for this version in your browser?",
            default=messagebox.NO,
        ):
            try:
                webbrowser.open(release_url)
            except Exception:  # noqa: BLE001
                pass

    def _close_progress_dialog(self) -> None:
        try:
            if hasattr(self, "_update_progress_bar"):
                self._update_progress_bar.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "_update_progress_dialog") and self._update_progress_dialog:
                self._update_progress_dialog.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _on_setup(self) -> None:
        # Setup needs stdin. Tk can't easily give a subprocess an
        # attached terminal, so for v1 we tell the user to use the
        # console exe for setup. Later we can render a proper Tk
        # dialog that writes runner_config.json directly.
        messagebox.showinfo(
            "Setup",
            "For this build, run the setup from a terminal:\n\n"
            f"  {Path(sys.executable).name} --setup\n\n"
            "A Tk setup dialog is on the roadmap — for now this opens "
            "the same prompts you saw on first launch.",
        )

    def _run_oneshot(self, extra_args: list[str], label: str) -> None:
        """Run the runner CLI in --foo mode, capture output, dump
        into the log panel. Used for --open-browser / --inspect-flow /
        --diagnose: short-lived commands that return promptly."""
        argv = _runner_invocation() + extra_args + ["--no-pause"]
        self._append_log(f"[gui] {label}: {' '.join(argv)}\n")
        try:
            res = subprocess.run(
                argv,
                **{k: v for k, v in _platform_popen_kwargs().items()
                   if k != "bufsize"},
                timeout=120,
            )
            self._append_log(
                f"[gui] {label} exit code: {res.returncode}\n",
                tag="ok" if res.returncode == 0 else "warn",
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[gui] {label} failed: {exc}\n", tag="error")

    # ----- Log capture (background thread → Tk event) -----------------

    def _start_reader_thread(self) -> None:
        if self.reader_thread is not None and self.reader_thread.is_alive():
            return
        self.reader_thread = threading.Thread(
            target=self._reader_thread_main,
            name="runner-stdout-reader",
            daemon=True,
        )
        self.reader_thread.start()

    def _reader_thread_main(self) -> None:
        """Read subprocess stdout line-by-line. Each line goes into
        the queue + raises a Tk event so the main thread wakes up
        and drains. Avoids any periodic polling — idle CPU stays
        low because there's literally no timer firing."""
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for raw in iter(proc.stdout.readline, ""):
            try:
                self.log_queue.put_nowait(raw)
            except queue.Full:
                # Drop oldest by draining one item, then add the new.
                try:
                    self.log_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.log_queue.put_nowait(raw)
                except queue.Full:
                    pass
            try:
                # Wake the Tk main thread. event_generate is the only
                # cross-thread-safe Tk call we use.
                self.root.event_generate("<<RunnerLog>>", when="tail")
            except (tk.TclError, RuntimeError):
                # Tk window closing — exit the reader.
                return
        # Subprocess closed its pipe → it has exited (or is about to).
        try:
            self.root.event_generate("<<RunnerLog>>", when="tail")
        except (tk.TclError, RuntimeError):
            pass

    def _drain_log_queue(self, _event: tk.Event | None = None) -> None:
        drained: list[str] = []
        while True:
            try:
                drained.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not drained:
            return
        merged = "".join(drained)
        # Classify each line for the colour tag.
        for line in merged.splitlines(keepends=True):
            tag = None
            stripped = line.strip()
            if " ERROR" in line or " FAIL" in stripped:
                tag = "error"
            elif " WARNING" in line or " WARN" in stripped:
                tag = "warn"
            elif "succeeded" in line.lower() or "[OK" in line:
                tag = "ok"
            self._append_log(line, tag=tag)

    def _append_log(self, line: str, *, tag: Optional[str] = None) -> None:
        # Append + trim to the max-line budget so the buffer doesn't
        # grow unbounded over a long session.
        self.log_text.configure(state=tk.NORMAL)
        if tag:
            self.log_text.insert(tk.END, line, tag)
        else:
            self.log_text.insert(tk.END, line)
        # Trim
        total = int(self.log_text.index("end-1c").split(".")[0])
        if total > self._max_log_lines:
            cut = total - self._max_log_lines
            self.log_text.delete("1.0", f"{cut}.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ----- Status state machine ---------------------------------------

    def _tick_process_status(self) -> None:
        """Detect subprocess exit (crash or clean) so the status
        flips automatically. Cheap: just polls Popen.poll() once a
        second."""
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            tag = "ok" if code in (0, 130) else "error"
            self._append_log(
                f"[gui] runner exited with code {code}\n", tag=tag,
            )
            self.proc = None
            self._set_status("stopped")
        self.root.after(1000, self._tick_process_status)

    def _set_status(self, state: str) -> None:
        self._current_status = state
        if state == "running":
            self.status_label.configure(text="● Running")
            self.status_label.configure(foreground=_OK)
            self.btn_start.state(["disabled"])
            self.btn_stop.state(["!disabled"])
        elif state == "working":
            self.status_label.configure(text="● Working")
            self.status_label.configure(foreground=_ACCENT)
            self.btn_start.state(["disabled"])
            self.btn_stop.state(["!disabled"])
        else:  # stopped
            self.status_label.configure(text="● Stopped")
            self.status_label.configure(foreground=_BAD)
            self.btn_start.state(["!disabled"])
            self.btn_stop.state(["disabled"])

    # ----- Window lifecycle -------------------------------------------

    def _on_quit(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            # Don't leave a runner orphaned behind a closed GUI.
            if not messagebox.askyesno(
                "Quit",
                "The runner is still running. Stop it and quit?",
                default=messagebox.YES,
            ):
                return
            self._on_stop()
        self.root.destroy()


# ---------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------

def main() -> int:
    root = tk.Tk()
    RunnerGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
