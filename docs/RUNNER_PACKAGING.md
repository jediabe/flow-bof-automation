# Building & distributing the FlowBOFRunner executable

End users should never need Docker, Git, Python, or `.env` files to
connect a runner to the hosted SaaS. This document describes how to
produce a standalone Windows exe (`FlowBOFRunner.exe`), where it
expects to live on a user's machine, and what's still missing before
we can hand it to a stranger.

The Docker-based developer flow (`docker compose run --rm app python
main.py --runner-poll`) is unchanged and still works alongside this.

## Supported platforms

| Platform | Output                                                 | Build script                            |
| -------- | ------------------------------------------------------ | --------------------------------------- |
| Windows  | `dist\FlowBOFRunner.exe` (~38 MB, single file)         | `scripts\build_runner_windows.ps1`      |
| macOS    | `dist/FlowBOFRunner.app` + `dist/FlowBOFRunner-mac-alpha.dmg` | `scripts/build_runner_mac.sh`     |
| Linux    | (not packaged; developers use `python runner_app.py`)  | n/a                                     |

Both packaged builds run without Docker, Python, or Git on the user's
machine. They expect the user to have Google Chrome installed; the
runner connects to it via CDP.

## Build (Windows)

Requirements on the build machine:

- Windows 10/11
- Python 3.10+ (`py -3.11` works; we don't depend on 3.12+ features)
- Google Chrome installed (we don't bundle it; the runner connects
  to the user's installed Chrome via CDP)

From the repo root:

```powershell
.\scripts\build_runner_windows.ps1
```

What the script does:

1. Creates `.venv-runner/` next to the repo (idempotent).
2. Installs `requirements-runner.txt` — a lean set without
   Streamlit / AI SDKs.
3. Runs `pyinstaller FlowBOFRunner.spec --clean --noconfirm`.
4. Prints the output path.

Output: `dist\FlowBOFRunner.exe` (single file, ~30 MB).

Verify:

```powershell
.\dist\FlowBOFRunner.exe --diagnose
```

## Build (macOS)

Requirements on the build machine:

- macOS (any Intel or Apple Silicon Mac running macOS 11+)
- Python 3.10+ — 3.11 or 3.12 strongly recommended. Python 3.14
  currently lacks prebuilt wheels for `greenlet` (via Playwright),
  forcing a source build that hits internal-API renames. Install
  Python 3.12 from python.org or `brew install python@3.12`.
- Google Chrome installed (not bundled)
- Xcode command-line tools (`xcode-select --install`) — pip falls
  back to source builds for some packages and needs the compiler

From the repo root:

```bash
chmod +x scripts/build_runner_mac.sh
./scripts/build_runner_mac.sh
```

What the script does:

1. Creates `.venv-runner/` next to the repo (idempotent).
2. Installs `requirements-runner.txt`.
3. Runs `pyinstaller FlowBOFRunner.spec --clean --noconfirm`.
4. Writes a `Flow BOF Runner.command` wrapper next to the `.app`
   that opens Terminal and runs the binary (more on why below).
5. Bundles both into `FlowBOFRunner-mac-alpha.dmg` via `hdiutil`.

Outputs:

- `dist/FlowBOFRunner.app` (the .app bundle)
- `dist/Flow BOF Runner.command` (Terminal launcher)
- `dist/FlowBOFRunner-mac-alpha.dmg`

Verify the build:

```bash
./dist/FlowBOFRunner.app/Contents/MacOS/FlowBOFRunner --diagnose
```

### Why the `.command` file?

The runner is interactive — it asks for a SaaS URL and runner token
on first launch, prints status to stderr, and listens for Ctrl-C.
Double-clicking a `.app` from Finder silently launches it with no
console: the stdin prompt has nowhere to come from, stderr goes
nowhere visible, and the user sees nothing happen.

macOS treats files ending in `.command` as "open in Terminal on
double-click". The build script writes a tiny shell wrapper that
runs the .app's inner binary inside a real Terminal window — that's
how alpha users start the runner. The .app remains usable from a
Terminal directly:

```bash
./dist/FlowBOFRunner.app/Contents/MacOS/FlowBOFRunner
```

or after dragging to `/Applications`:

```bash
/Applications/FlowBOFRunner.app/Contents/MacOS/FlowBOFRunner --diagnose
```

CLI flags (`--diagnose`, `--reset-config`, `--saas-url`,
`--runner-token`, `--no-pause`) all work the same as on Windows.

### Gatekeeper / unsigned `.app`

The build is unsigned and unnotarised for alpha. First-launch on a
fresh Mac triggers Gatekeeper:

> "Flow BOF Runner cannot be opened because the developer cannot
> be verified."

Two ways for a tester to work around it:

1. **Right-click → Open** (then click **Open** again in the dialog).
   This is per-app; once approved, double-click works thereafter.
2. **System Settings → Privacy & Security → "Open Anyway"** right
   after Gatekeeper has blocked it.

Same caveat applies to the `.command` wrapper on first run.

Document the click path in your release notes. Roadmap: Apple
Developer ID signing + notarisation eliminates this prompt entirely.

## What ends up in the exe

Configured in `FlowBOFRunner.spec`:

- `runner_app.py` + `src/runner_app/*`
- `src/agent_api.py` + every job handler it pulls in (Playwright,
  Flow automation, recorded-flow scripts, etc.)
- `httpx` + `python-dotenv`

Deliberately **excluded** (kept out of the bundle):

- Streamlit (the dev UI lives separately)
- OpenAI / Anthropic SDKs — AI prompt generation runs in the SaaS,
  not on the runner
- `openpyxl`, `streamlit_paste_button`
- `torch`, `tensorflow` (PyInstaller occasionally picks these up
  through transitive scans)

If a future job handler adds a hard dependency, add it to:

1. `requirements-runner.txt`
2. The `hidden_imports` list in `FlowBOFRunner.spec`

## What the runner expects on the user's machine

| Thing               | Required? | Where the runner looks                                                                                                   |
| ------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------ |
| Google Chrome       | yes       | `C:\Program Files\Google\Chrome\Application\chrome.exe`, `Program Files (x86)`, `%LOCALAPPDATA%\…`                       |
| Internet access     | yes       | Outbound HTTPS to the configured SaaS URL                                                                                |
| FlowBOF Chrome profile | created   | `%LOCALAPPDATA%\FlowBOF\ChromeProfile` (dedicated; never touches the user's normal Chrome)                              |
| Runner config       | created   | `%APPDATA%\FlowBOF\runner_config.json`                                                                                   |
| Job event log       | n/a       | The runner streams events back to the SaaS; nothing persistent is kept locally beyond `data/agent_cache/`                |

Mac/Linux paths are in `src/runner_app/paths.py` — packaging support
for those platforms is a later milestone.

## Distribution (manual, alpha)

Until code signing + auto-update land, we hand out the builds
through GitHub Releases:

1. Tag the release on GitHub: `runner-v0.x.y`.
2. Build on each platform separately:
   - Windows: `scripts\build_runner_windows.ps1` → `dist\FlowBOFRunner.exe`
   - macOS:   `scripts/build_runner_mac.sh` → `dist/FlowBOFRunner-mac-alpha.dmg`
3. Rename the artefacts for clarity before upload:
   - `FlowBOFRunner-windows-alpha.exe`
   - `FlowBOFRunner-mac-alpha.dmg`
4. Upload both to the same GitHub Release.
5. Point the SaaS at them via env vars on the production deploy:
   ```
   NEXT_PUBLIC_RUNNER_WINDOWS_RELEASE_URL=https://github.com/<owner>/<repo>/releases/download/runner-v0.x.y/FlowBOFRunner-windows-alpha.exe
   NEXT_PUBLIC_RUNNER_MAC_RELEASE_URL=https://github.com/<owner>/<repo>/releases/download/runner-v0.x.y/FlowBOFRunner-mac-alpha.dmg
   ```
   The Runner Setup page reads these and shows real download links.
   When `NEXT_PUBLIC_RUNNER_MAC_RELEASE_URL` is unset, the Mac card
   renders a disabled "Mac Runner coming soon" button instead.

First-run UX caveats to call out in the release notes:

- **Windows SmartScreen warning.** The exe is unsigned. The user
  has to click **More info → Run anyway** the first time. Code
  signing fixes this — see "Roadmap" below.
- **First launch is slow.** PyInstaller's onefile bootloader
  extracts the bundle into `%TEMP%` on every run (a few seconds on
  SSD, longer on HDD). The subsequent steady-state behaviour is
  fine.
- **Anti-virus false positives.** Unsigned PyInstaller bundles
  occasionally trip Windows Defender / Norton heuristics. Adding a
  code-signing certificate is the standard fix; in the meantime
  the user may need to allow-list `dist\FlowBOFRunner.exe`.

## Roadmap

- **Code signing.** Get an Authenticode certificate; integrate
  `signtool` into `build_runner_windows.ps1`. Eliminates the
  SmartScreen prompt + most AV false positives.
- **Auto-update.** A simple "check GitHub Releases for a newer
  version" probe at startup. We'd ship the version inside the exe
  and compare against the `runner-vX.Y.Z` tags.
- **Tray app.** Wrap the runner in a Tauri or pywebview shell so it
  lives in the system tray instead of a console window. The
  console-mode build today is the simplest thing that works.
- **macOS signing + notarisation.** The current `.dmg` is unsigned;
  testers have to right-click → Open the first time. Apple Developer
  ID signing + `notarytool` submission removes the Gatekeeper prompt
  entirely. Roughly $99/year for the Apple developer account.
- **Custom `.icns` icon + dmg background.** Today the .app uses the
  generic Python icon and the .dmg is a plain list view. Polish.
- **Linux AppImage.** Lower priority; the developer Docker path
  already covers this.

## Dedicated Chrome profile + browser lifecycle

The runner always launches Chrome with a **dedicated `--user-data-dir`**:

- Windows: `%LOCALAPPDATA%\FlowBOF\ChromeProfile`
- macOS:   `~/Library/Application Support/FlowBOF/chrome-profile`

That directory is separate from the user's normal Chrome profile by
design. Practical consequences:

- Closing the runner's debug Chrome window does **not** affect any
  other Chrome window on the system, and vice versa.
- The runner never reads or modifies the user's bookmarks, history,
  saved passwords, extensions, or normal Chrome session cookies.
- If the user's normal Chrome was already running before they
  launched the runner, both Chrome instances coexist cleanly —
  they're different OS processes pointing at different profile dirs.
- The runner **never** issues a process-wide kill (no `Stop-Process
  chrome`, no `pkill chrome`). The only Chromes it touches are the
  ones it spawned itself, against the dedicated profile.

### Reopening the Flow window

If the user accidentally closes the runner's dedicated Chrome
window, two clean ways to bring it back:

- **From the menu:** choose `Open/Reopen Google Flow browser`.
- **From the command line:** `FlowBOFRunner --open-browser` (or
  `python runner_app.py --open-browser` from source).

Behaviour, top-down:

1. If the debug Chrome's CDP endpoint is still reachable and a Flow
   tab is open, the helper **activates** that tab — no new process,
   no flicker.
2. If CDP is reachable but no Flow tab is open, the helper opens
   one via the CDP `PUT /json/new` endpoint — still no new process.
3. If CDP isn't reachable at all (the Chrome window was actually
   closed), the helper cold-launches a fresh debug Chrome against
   the dedicated profile, exactly as the runner does on startup.

None of those branches touch the user's normal Chrome.

## Flow UI prep is inside the bundle

Both the Windows `.exe` and the macOS `.app` ship the centralised
Flow-UI prep helper (`src/flow_ui_prep.py`). Every image / video
submit dispatched through the runner — Docker dev path, connected
runner polling, or the packaged binary — runs the same pass:

1. Dismiss stale overlays / menus / dialogs.
2. Close any agent prompt suggestion pills.
3. Re-apply Flow's image generation settings (9:16, 1x, Nano
   Banana Pro) before image jobs.

The behaviour is identical across all distribution channels — the
prep code is part of `src/agent_api.py`'s handler table, not a
build-script add-on. See
[CONNECTED_RUNNER.md → Automatic Flow UI prep](CONNECTED_RUNNER.md#automatic-flow-ui-prep)
for the operator-only env switches and the per-step rules.

## Console lifecycle (the window stays open)

Both the Windows `.exe` and the macOS `.command` wrapper run in
**console mode** so the user can see the live status stream and
any errors. The window doesn't auto-close on errors.

| Exit reason                       | Behaviour                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------ |
| Menu → Exit (clean shutdown)      | Window closes immediately                                                      |
| `--diagnose` with all green       | Window closes immediately                                                      |
| Any error / crash                 | Message printed, then `Press Enter to close.` — Enter to dismiss               |
| Ctrl-C / SIGINT during a job      | In-flight job finishes, then `Runner stopped. Press Enter to exit.`            |
| `--no-pause` set                  | Never pauses, exits immediately (useful for CI / build smoke-tests)            |

Reasoning: the alpha runner is interactive (stdin prompts) and the
user needs to see what happened. Auto-closing on error would hide
the message; auto-closing on Ctrl-C would make the user wonder if
they really stopped the runner.

## Developer paths that still work

These are **not** removed — they're how we iterate without
rebuilding the exe every time:

| Command                                                  | When to use                                                        |
| -------------------------------------------------------- | ------------------------------------------------------------------ |
| `python runner_app.py`                                   | Run the new standalone app from source (same behaviour as the exe) |
| `python runner_app.py --diagnose`                        | One-shot health check; never touches Chrome                        |
| `python main.py --runner-poll`                           | Legacy env-driven poller (Docker / CLI flag-based)                 |
| `docker compose run --rm app python main.py --runner-poll` | Containerised dev poller (Docker is **not** required for end users) |
| `.\scripts\start_chrome_debug.ps1`                       | Launch debug Chrome separately (only needed for the legacy paths)  |

End users should be using `FlowBOFRunner.exe`. Everything else is a
developer convenience.
