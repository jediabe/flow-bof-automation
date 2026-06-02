# Job protocol — design

Wire format between hosted SaaS and the local agent. JSON over either
HTTPS (polling) or WebSocket (push). The agent must accept both; the
payloads below are transport-agnostic.

Status: **draft**. Versioned at the message level via `protocol_version`
so we can evolve safely. Current version: **1**.

## Common envelope

Every message uses this outer shape:

```json
{
  "protocol_version": 1,
  "message_id":       "01JBZ...",
  "ts":               "2026-06-02T18:22:31Z",
  "type":             "job" | "progress" | "result" | "error" | "ping" | "pong",
  "payload":          { ... }
}
```

- `message_id`: ULID. Idempotency key — agent refuses to re-execute a
  job whose `message_id` it has already processed. The agent persists
  the last N completed message_ids in `~/.flow-bof-agent/state.json`.
- `ts`: ISO-8601 UTC. SaaS-issued for `type=job`, agent-issued for
  `progress` / `result` / `error`.
- `type`: routing tag.

When this doc shows a "request JSON" or "response JSON" the snippet is
the value of `payload`, not the whole envelope.

## Job types

| `job.type`                              | Direction          | Trigger                                          |
| --------------------------------------- | ------------------ | ------------------------------------------------ |
| `health_check`                          | SaaS → agent       | Periodic; also on every dashboard load.          |
| `check_flow_connection`                 | SaaS → agent       | User clicks "Check Browser" in dashboard.        |
| `import_assets`                         | SaaS → agent       | After Kalodata upload that needs local processing (rare). |
| `generate_flow_images`                  | SaaS → agent       | User clicks "Generate Images".                   |
| `scan_favorited_images`                 | SaaS → agent       | User clicks "Scan Favorites".                    |
| `generate_flow_videos_from_favorites`   | SaaS → agent       | User clicks "Generate Videos".                   |
| `download_flow_videos`                  | SaaS → agent       | (Future) export step.                            |
| `create_tiktok_draft_later`             | SaaS → agent       | (Future) scheduled posting.                      |

Each job carries:

```json
{
  "job_id":           "01JBZ...",
  "job_type":         "generate_flow_images",
  "org_id":           "org_...",
  "workspace_id":     "ws_...",
  "issued_at":        "2026-06-02T18:22:31Z",
  "timeout_seconds":  1800,
  "params":           { /* type-specific, see below */ }
}
```

## Common response shapes

### Progress event (agent → SaaS)

```json
{
  "job_id":     "01JBZ...",
  "stage":      "submitting",
  "message":    "Submitting image 3 of 8 (id=ab12cd)",
  "completed":  3,
  "total":      8,
  "extras":     { /* per-job-type fields */ }
}
```

`stage` is a short token from a finite per-job-type set, so the
dashboard can render a real progress bar.

### Final result (agent → SaaS)

```json
{
  "job_id":     "01JBZ...",
  "status":     "succeeded" | "succeeded_with_failures" | "failed",
  "summary":    { /* per-job-type counts */ },
  "elapsed_s":  184.3,
  "logs_url":   "https://saas.example.com/logs/01JBZ.../log.txt"
}
```

`status = "succeeded_with_failures"` means the overall run completed
but at least one row failed — same semantics as today's
"video batch done in 184.1s — 7 submitted, 1 failed".

### Error (agent → SaaS)

Used when the runner couldn't even attempt the job, or when something
catastrophic interrupts it mid-run.

```json
{
  "job_id":      "01JBZ...",
  "code":        "chrome_unreachable" | "flow_not_logged_in" | "agent_panic"
                 | "unknown_job_type" | "protocol_version_mismatch"
                 | "timeout" | "internal_error",
  "message":     "Chrome debugger at http://cdp-proxy:9333 didn't respond within 5s.",
  "details":     { /* optional structured fields */ },
  "stack":       "Traceback (most recent...)\n  File \"...\""   /* optional */
}
```

Codes are stable; new ones get added with monotonic care. The dashboard
maps `code` → user-friendly message + suggested fix.

---

## Job type: `health_check`

Smallest possible round-trip. Confirms the agent is alive, can run a
job, and can reach Chrome.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "health_check",
  "params":   {}
}
```

### Progress
None — the job is instantaneous. Agent jumps straight to result.

### Result
```json
{
  "job_id":   "01JBZ...",
  "status":   "succeeded",
  "summary": {
    "agent_version":      "0.4.2",
    "os":                 "darwin-arm64",
    "chrome_reachable":   true,
    "flow_logged_in":     true,
    "blanket_video_prompt_set": true
  },
  "elapsed_s": 0.4
}
```

### Errors
- `chrome_unreachable` — CDP proxy didn't respond.
- `internal_error` — anything else.

---

## Job type: `check_flow_connection`

Like `health_check` but actively navigates the user's Flow tab to
confirm sign-in.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "check_flow_connection",
  "params":   {
    "flow_url": "https://labs.google/flow"
  }
}
```

### Progress
```json
{ "stage": "opening_tab",  "message": "Navigating to Flow Labs..." }
{ "stage": "verifying",    "message": "Looking for signed-in indicators..." }
```

### Result
```json
{
  "status": "succeeded",
  "summary": {
    "flow_logged_in":   true,
    "active_project":   "Untitled (default)",
    "credit_balance":   "n/a",
    "model_selected":   "Veo 3"
  }
}
```

### Errors
- `chrome_unreachable`
- `flow_not_logged_in` — sign-in page detected; user must log in.
- `flow_unreachable` — labs.google didn't load.

---

## Job type: `import_assets`

Used when the SaaS wants to push assets (reference images) to the
agent's local cache ahead of a later run. Most assets are downloaded
on-demand at job time, so this job type is rare and mostly for
preloading.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "import_assets",
  "params": {
    "assets": [
      {
        "asset_id":     "asset_01JBZ...",
        "kind":         "reference_image",
        "filename":     "boots_serum_primary.jpg",
        "download_url": "https://r2.example.com/sig/...",
        "sha256":       "ab12...ef89"
      }
    ]
  }
}
```

### Progress
```json
{ "stage": "downloading", "completed": 5, "total": 12,
  "message": "Downloading boots_serum_primary.jpg" }
```

### Result
```json
{
  "status": "succeeded",
  "summary": { "downloaded": 12, "skipped": 0, "failed": 0,
               "cache_bytes": 14523412 }
}
```

### Errors
- `download_failed` — at least one signed URL 4xx/5xx'd.
- `checksum_mismatch` — SHA-256 didn't match.
- `disk_full`.

---

## Job type: `generate_flow_images`

The current "Generate Images" button, lifted to a job.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "generate_flow_images",
  "params": {
    "batch_id":          "batch_01JBZ...",
    "limit":             30,
    "automation_mode":   "fast" | "balanced",
    "image_fast_submit_mode": true,
    "rows": [
      {
        "product_id":        "prod_01JBZ...",
        "row_id":            "01",
        "product_name":      "Slim fit shapewear bodysuit",
        "image_prompt":      "<four-paragraph UK or US prompt>",
        "reference_image":   {
          "asset_id":     "asset_01JBZ...",
          "filename":     "shapewear_primary.jpg",
          "download_url": "https://r2.example.com/sig/..."
        }
      }
    ]
  }
}
```

### Progress
```json
{ "stage": "preparing",      "message": "Opening Flow Labs..." }
{ "stage": "downloading",    "completed": 2, "total": 30,
  "message": "Downloading reference image for prod_..." }
{ "stage": "submitting",     "completed": 3, "total": 30,
  "message": "Submitting image 3 of 30: Slim fit shapewear bodysuit",
  "extras": { "product_id": "prod_...", "row_id": "03" } }
{ "stage": "captured",       "completed": 3, "total": 30,
  "extras": { "product_id": "prod_...", "tile_id": "tile_...",
              "media_id": "ab12..." } }
{ "stage": "row_failed",     "extras": {
    "product_id": "prod_...",
    "reason": "'Add to Prompt' button stayed disabled for 45s"
  } }
```

### Result
```json
{
  "status": "succeeded_with_failures",
  "summary": {
    "submitted":   29,
    "failed":      1,
    "captures":    [
      { "product_id": "prod_...", "row_id": "01",
        "tile_id": "tile_...", "media_id": "ab12..." }
    ],
    "failures": [
      { "product_id": "prod_...", "row_id": "17",
        "reason": "Add to Prompt button stayed disabled for 45s" }
    ]
  },
  "elapsed_s": 1843.2
}
```

### Errors
- `chrome_unreachable`
- `flow_not_logged_in`
- `protocol_version_mismatch` — params shape unknown.
- `timeout` — entire job exceeded `timeout_seconds`.

---

## Job type: `scan_favorited_images`

Read-only DOM scan of the Flow grid. No writes.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "scan_favorited_images",
  "params":   {}
}
```

### Progress
```json
{ "stage": "scanning", "message": "Scanning Flow grid..." }
```

### Result
```json
{
  "status": "succeeded",
  "summary": {
    "tiles_total":          145,
    "favorited":            12,
    "favorited_images":     11,
    "favorited_videos":     1,
    "already_submitted":    4,
    "ready_for_video":      7,
    "tiles": [
      {
        "tile_id":  "tile_...",
        "media_id": "ab12...",
        "edit_id":  "edit_...",
        "favorited": true,
        "kind":     "image"
      }
    ]
  }
}
```

### Errors
- `chrome_unreachable`
- `flow_not_logged_in`

---

## Job type: `generate_flow_videos_from_favorites`

The current "Generate Videos from Favorited Images" button.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "generate_flow_videos_from_favorites",
  "params": {
    "batch_id":               "batch_01JBZ...",
    "limit":                  30,
    "automation_mode":        "fast",
    "include_already_submitted": false,
    "blanket_video_prompt":   "Slow handheld iPhone-style push-in..."
  }
}
```

### Progress
```json
{ "stage": "scanning",     "message": "Scanning favorited tiles..." }
{ "stage": "preparing",    "completed": 0, "total": 7,
  "message": "Found 7 favorited image(s) eligible" }
{ "stage": "submitting",   "completed": 3, "total": 7,
  "message": "Submitting video 3 of 7",
  "extras": { "media_id": "ab12..." } }
{ "stage": "row_failed",   "extras": {
    "media_id": "cd34...",
    "reason": "menu did not appear after right-click"
  } }
```

### Result
```json
{
  "status": "succeeded_with_failures",
  "summary": {
    "favorited_found":         7,
    "submitted":               6,
    "failed":                  1,
    "already_submitted_skipped": 4,
    "submitted_media_ids":     ["ab12...", "ef56..."],
    "failed_media_ids":        [
      { "media_id": "cd34...",
        "reason":   "menu did not appear after right-click" }
    ]
  },
  "elapsed_s": 423.1
}
```

### Errors
- `chrome_unreachable`
- `flow_not_logged_in`
- `blanket_prompt_empty` — `params.blanket_video_prompt` was empty.

---

## Job type: `download_flow_videos` (future)

Pulls finished video URLs from Flow's gallery and uploads them to
SaaS-side object storage. Not implemented yet — current product flow
keeps the assets in Flow.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "download_flow_videos",
  "params": {
    "media_ids":      ["ab12...", "ef56..."],
    "upload_target":  "https://r2.example.com/sig/upload/..."
  }
}
```

### Progress
```json
{ "stage": "downloading", "completed": 1, "total": 5 }
{ "stage": "uploading",   "completed": 1, "total": 5 }
```

### Result
```json
{
  "status": "succeeded",
  "summary": {
    "downloaded": 5,
    "uploaded":   5,
    "videos": [
      { "media_id": "ab12...",
        "asset_id": "asset_01JBZ...",
        "duration_s": 8.2,
        "bytes": 14_523_412 }
    ]
  }
}
```

### Errors
- `flow_export_unavailable` — Flow's download button didn't appear.
- `upload_failed`.

---

## Job type: `create_tiktok_draft_later` (future)

Headed automation against TikTok Studio in the same Chrome profile.

### Request
```json
{
  "job_id":   "01JBZ...",
  "job_type": "create_tiktok_draft_later",
  "params": {
    "video": {
      "source":   "flow_media_id" | "asset_url",
      "media_id": "ab12...",
      "url":      null
    },
    "caption":           "Found this at the shop and had to try it 🛍️",
    "hashtags":          ["#tiktokshop", "#fyp"],
    "schedule_at":       "2026-06-04T18:00:00Z",
    "product_link":      "https://shop.tiktok.com/..."
  }
}
```

### Progress
```json
{ "stage": "opening_studio",  "message": "Opening TikTok Studio..." }
{ "stage": "uploading",       "completed": 1, "total": 1 }
{ "stage": "filling_caption" }
{ "stage": "saving_draft" }
```

### Result
```json
{
  "status": "succeeded",
  "summary": {
    "draft_id":     "draft_...",
    "preview_url":  "https://www.tiktok.com/...",
    "scheduled_at": "2026-06-04T18:00:00Z"
  }
}
```

### Errors
- `tiktok_not_logged_in`
- `tiktok_upload_failed`
- `tiktok_studio_unreachable`

---

## Reserved fields

The agent must:
- **Ignore unknown top-level fields** in any payload. Forward-compat.
- **Reject unknown `job.type`** values with `code: "unknown_job_type"`.
- **Reject unknown `protocol_version`** with `code: "protocol_version_mismatch"`.

The SaaS must:
- Never reissue the same `message_id` with different content. Idempotency.
- Treat unknown progress `stage` tokens as informational — never block
  rendering.

## Versioning policy

`protocol_version` bumps when:
- A field is renamed or removed.
- A field's type changes.
- A new required field is added.

Adding new optional fields, new job types, or new progress stages is
**not** a version bump. Old agents see them and ignore them.
