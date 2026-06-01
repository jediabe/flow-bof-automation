# Recording Flow Labs actions

We stopped guessing selectors. Instead: record one successful run with Playwright's Inspector, paste the emitted locators into [src/recorded_flow.py](../src/recorded_flow.py), and the rest of the automation reuses them on every run.

## Prerequisites

- `BROWSER_MODE=remote_debugging` in `.env`.
- Chrome already running with `--remote-debugging-port=9222`:
  ```powershell
  "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\chrome-flow-automation"
  ```
- You are signed into Flow Labs in that Chrome window.
- A sample product image saved locally — you'll use it during the recording.

Verify the setup with `python main.py --check-browser`.

## Recording walkthrough

1. **Start the recorder.**
   ```powershell
   python scripts/record_flow_actions.py
   ```
   The script connects to your live Chrome over CDP, reuses the open Flow Labs tab (or opens one if needed), and calls `page.pause()`. The Playwright Inspector window opens.

2. **Click "Record" in the Inspector.** The button is at the top of the Inspector window. While recording, every click/type you perform on Flow Labs is emitted as a Python line in the Inspector's right pane.

3. **Perform the full Flow Labs flow once.** Order matters — these are the steps you need to record:

   1. Upload a product image (use the upload button in the asset panel).
   2. Click the `+` button inside the prompt composer.
   3. Click the **Add to Prompt** menu item.
   4. Click into the prompt input and type a short test prompt.
   5. Click the bottom-right send/generate arrow.

   The Inspector will emit something like:
   ```python
   page.get_by_role("button", name="Upload").click()
   page.get_by_role("button", name="Add").click()
   page.get_by_role("menuitem", name="Add to Prompt").click()
   page.get_by_placeholder("What do you want to create?").fill("a test")
   page.get_by_role("button", name="Generate").click()
   ```

4. **Stop the recorder** (click Record again) and **resume** the script (the Resume button in the Inspector) so it can exit cleanly. The script will disconnect from Chrome without closing your window.

5. **Copy the emitted locators into [src/recorded_flow.py](../src/recorded_flow.py).** Each step has its own `_locate_*` function with a short comment showing what Playwright typically emits. Replace the function body with the locator portion of the recorded line:

   | Recorded line | Function to update |
   | --- | --- |
   | `page.get_by_role("button", name="Add").click()` | `_locate_plus_button` → `return page.get_by_role("button", name="Add")` |
   | `page.get_by_role("menuitem", name="Add to Prompt").click()` | `_locate_add_to_prompt` |
   | `page.get_by_placeholder("What do you want to create?").fill(...)` | `_locate_prompt_input` |
   | `page.get_by_role("button", name="Generate").click()` | `_locate_generate_arrow` |

   Do not paste the `.click()` / `.fill()` — only the locator expression. The orchestration in `perform_recorded_flow` does the clicking and filling.

6. **Optional — tighten the result locators.** Open the Inspector's "Pick locator" tool (target icon) and hover the generated result image and the in-progress spinner. Update `_locate_result_images` and `_locate_generation_in_progress` if Flow's defaults don't match.

## Verifying

Run a single end-to-end generation:

```powershell
python main.py --run-one --product-index 0
```

What success looks like in the log:
```
Uploaded image
Clicked composer plus button
Clicked Add to Prompt — reference thumbnail attached
Prompt inserted
Clicked generate arrow
New result image detected
Saved outputs/images/.../slim-fit-shapewear_001.png
```

Each line is gated on a state check — if you see `Prompt inserted` the prompt text is actually in the composer, and if you see `Clicked generate arrow` a generation request or new result tile was observed afterwards. If a step fails, an error screenshot lands in `outputs/logs/`.

## When the recording goes stale

Re-record. The Flow Labs DOM is the only source of truth. Don't try to patch individual selectors by hand — diff `recorded_flow.py` after a fresh recording. That's the whole point of this seam.

## Troubleshooting

- **"page.pause() didn't open Inspector."** Set `PWDEBUG=1` in the shell and re-run the script — the helper sets it for you but some environments override it.
- **"Inspector opened but Record button is disabled."** Inspector recording requires Playwright ≥ 1.40. Check `pip show playwright`. If older, upgrade with `pip install -U playwright && playwright install chromium`.
- **"Codegen emits CSS selectors I don't recognise."** That's fine — paste them as-is. The `_locate_*` functions return a `Locator`, and `page.locator("...")` accepts any selector engine string.
- **"Google blocked sign-in during recording."** You should be recording inside the manually-launched remote-debugging Chrome, not Playwright's bundled Chromium. Re-read the Prerequisites section.
