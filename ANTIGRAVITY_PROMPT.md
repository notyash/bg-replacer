# Build Spec: AI Background Replacer — Web App

## What this is
A web app where a user uploads a photo of themselves and types a short
description of the background they want (e.g. "rooftop at night",
"cozy coffee shop"). The app automatically:

1. Analyzes the photo (edge complexity, background contamination,
   lighting) using Gemini.
2. Expands the user's vague background description into a full
   positive/negative Stable Diffusion prompt using Gemini.
3. Injects both into a pre-built ComfyUI workflow (mask-based
   background inpainting) and runs it via the ComfyUI API.
4. Shows the result in the browser with a download button.

This is a BYOK (bring-your-own-key) tool: each user supplies their own
ComfyUI endpoint + Gemini API key. We are not hosting/paying for GPU
inference ourselves at this stage.

---

## Repo layout (already scaffolded — build inside this structure)

```
bg-replacer/
  backend/
    app/
      analyze_image.py     # ALREADY WRITTEN — Gemini image analysis
      expand_prompt.py     # ALREADY WRITTEN — Gemini prompt expansion
      comfyui_client.py    # ALREADY WRITTEN — ComfyUI API client
      main.py              # TO BUILD — FastAPI app tying it together
      config.py            # TO BUILD — settings/env handling
    requirements.txt       # TO BUILD
    Dockerfile             # TO BUILD
  frontend/
    (TO BUILD — see GUI spec below)
  workflows/
    background_replacer.json   # ALREADY EXPORTED — real ComfyUI API-format
                                # workflow, node titles already match what
                                # comfyui_client.py expects (see below)
  README.md               # TO BUILD
  .env.example            # TO BUILD
  .gitignore              # TO BUILD
```

## Existing code you must build around, not replace

### `backend/app/analyze_image.py`
`analyze(image_path, api_key) -> dict` — sends the photo to Gemini,
returns JSON with: `edge_complexity`, `background_contamination`,
`lighting_direction`, `lighting_quality`, `recommended_feather_px`,
`recommended_bg_mask_grow_px`, `lighting_prompt_fragment`.

**Known gap to close:** this does NOT yet detect reflective surfaces
(helmet visors, glasses, glass) which are a known matting failure mode
(confirmed via manual testing — reflective visors create holes in the
mask). Add a `"reflective_surface_detected": true|false` field to the
returned JSON and the analysis prompt inside the file. When true, the
backend should flag the job for manual review rather than fully
auto-completing (see API behavior below).

### `backend/app/expand_prompt.py`
`expand(user_request, lighting_fragment, api_key) -> dict` — returns
`{"positive_prompt": str, "negative_prompt": str}`. Negative prompt
always includes a baseline (extra people, bags/straps, artifacts) plus
scene-specific additions Gemini suggests.

### `backend/app/comfyui_client.py`
`ComfyUIClient(server_address)` with:
- `load_workflow(path)` — loads the exported JSON
- `apply_params(workflow, params)` — edits nodes **by their `_meta.title`**,
  not numeric ID (titles: `"Load Image"`, `"Positive Prompt"`,
  `"Negative Prompt"`, `"BgGrowMask"`, `"BgFeatherMask"`, `"KSampler"`
  for seed, `"OutputImage"` for the result node)
- `upload_image(path)` — for local ComfyUI instances (multipart upload)
- `queue_prompt(workflow)` — POSTs to `/prompt`, returns `prompt_id`
- `wait_for_result(prompt_id, output_node_title, workflow)` — polls
  `/history/{id}` until done
- `download_image(image_ref, save_path)` — fetches via `/view`

**Important real-world detail:** on SeaArt-hosted ComfyUI, the
`LoadImage` node's `image` field accepts a **direct URL string**
(confirmed from a real export — see `workflows/background_replacer.json`,
node `"2"`). On a local ComfyUI instance, you must `upload_image()`
first and pass the returned filename instead. `comfyui_client.py`
already exposes both paths (`source_image_url` vs
`source_image_filename` in `apply_params`) — the backend must detect
which mode to use (see config below) and call the right one.

### `workflows/background_replacer.json`
The real, working ComfyUI graph: `LoadImage` → `BRIAAI Matting` →
`GrowMask` (title `BgGrowMask`, supports **negative** expand values —
this is deliberate, see note in `comfyui_client.py`) → `FeatherMask`
(title `BgFeatherMask`) → `InvertMask` → `VAEEncodeForInpaint` →
`KSampler` → `VAEDecode` → `ImageCompositeMasked` → `PreviewImage`
(title `OutputImage`). Do not restructure this graph — only the
backend should ever touch node input values, never the graph shape.

---

## Backend to build: `main.py` (FastAPI)

### Config (`config.py`)
Per-request or per-session, not global env vars, since this is BYOK:
- `comfyui_server_address` (e.g. `127.0.0.1:8188` or a SeaArt-provided
  host:port)
- `comfyui_mode`: `"url"` (pass image URLs directly, SeaArt-style) or
  `"upload"` (local ComfyUI — upload file first)
- `gemini_api_key`

Support these via request headers or a per-session config object —
**never persist user API keys server-side** beyond the lifetime of a
single request/job (BYOK security expectation). Document this clearly
in the README's privacy section.

### Endpoints
- `POST /api/generate`
  - Accepts: uploaded image file (multipart), `background_description`
    (string), and the BYOK config (comfyui_server_address, comfyui_mode,
    gemini_api_key) — via headers or form fields, your call, but keep
    keys out of query strings/logs.
  - Flow:
    1. Save upload to a temp path.
    2. Call `analyze_image.analyze()`.
    3. If `reflective_surface_detected` is true, still proceed but
       include a `"warning"` field in the final response telling the
       user the mask may need manual touch-up around reflective areas
       (visor/glasses/glass) — do not block the job, just warn.
    4. Call `expand_prompt.expand()` with the user's
       `background_description` and the analyzed `lighting_prompt_fragment`.
    5. Build params dict for `comfyui_client.apply_params()`:
       `positive_prompt`, `negative_prompt`,
       `bg_grow_px` = `recommended_bg_mask_grow_px` (remember: this
       workflow wants this value **negated** if it's expressed as
       "grow the subject inward" — confirm sign convention against
       `comfyui_client.py`'s comment before wiring, do not silently
       flip signs), `bg_feather_px` = `recommended_feather_px`,
       `seed` = random int, and either `source_image_url` or
       `source_image_filename` depending on `comfyui_mode`.
    6. `queue_prompt()` → `wait_for_result()` → `download_image()`.
    7. Return the output image (base64 or a served static URL) plus
       the analysis JSON and any warnings.
  - Handle and surface errors clearly: bad ComfyUI address (connection
    refused), invalid Gemini key (auth error from Gemini SDK), job
    timeout, missing node title (means the user's workflow.json doesn't
    match — tell them plainly rather than a raw KeyError).
- `GET /api/health` — simple liveness check.

### Job handling
Since ComfyUI generation takes real time (10s–60s+), don't block a
single synchronous request indefinitely without feedback. Simplest
acceptable approach for v1: keep it synchronous but stream progress via
Server-Sent Events or WebSocket (ComfyUI's own `/ws` endpoint pushes
progress events you can relay) so the frontend can show a progress bar
instead of a blank spinner. If that's too much for v1, a plain
synchronous request with a generous timeout is an acceptable fallback
— note this as a "v2 improvement" in the README rather than skipping
silently.

---

## Frontend to build

Simple single-page app (React or plain HTML/JS — your choice, but
React + Vite is a reasonable default given the rest of the stack).

### Layout
1. **Settings panel** (collapsible, top of page): fields for ComfyUI
   server address, mode (URL/upload — a dropdown or auto-detect toggle),
   and Gemini API key. Store these in browser localStorage (client-side
   only) so returning users don't re-enter them — never send them
   anywhere except as part of the `/api/generate` request itself.
2. **Main panel**:
   - Image upload (drag-and-drop + file picker), with a preview
     thumbnail.
   - Text input: "Describe the background you want" (placeholder
     example: "rooftop at sunset").
   - "Generate" button — disabled until an image + description +
     required settings are present.
   - While running: progress indicator (tied to the SSE/WebSocket
     progress if implemented, else a generic spinner with elapsed time).
   - Result: side-by-side original vs. output, or a before/after
     slider if you want to be fancy (not required for v1).
   - Download button for the final image.
   - If the response includes a `warning` (e.g. reflective surface
     detected), show it as a small non-blocking notice near the result,
     not a modal/blocker.
3. Basic error states: show the backend's error message plainly if a
   job fails (bad key, ComfyUI unreachable, timeout).

Keep the visual design clean and minimal — this is a functional tool,
not a marketing site. No need for elaborate branding for v1.

---

## Deployment / hosting notes for Antigravity to account for
- Backend: containerize with the Dockerfile so it can run anywhere
  (Render, Fly.io, Railway, a VPS — user's choice later, don't hardcode
  a specific platform's config).
- Frontend: static build, deployable to any static host (Vercel,
  Netlify, GitHub Pages, or served by the same backend container).
- Because this is BYOK, the hosted app itself needs no GPU and no
  Gemini/ComfyUI billing — it's just a thin orchestrator. Make sure
  nothing in the code assumes a fixed/local ComfyUI address; it must
  always come from user-supplied config.
- `.env.example` should only contain non-secret defaults (e.g. default
  port, default timeout) — never a real API key.

---

## Explicitly out of scope for v1 (note in README as future work, don't build)
- Multi-user accounts / auth
- Server-side storage of generated images beyond the single request
- The reflective-surface manual-touch-up UI itself (v1 just warns the
  user; an actual in-browser mask editor is future work)
- Video/batch processing
