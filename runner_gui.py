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

import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
from pathlib import Path
from tkinter import ttk
from typing import Optional


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
