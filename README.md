# bg-replacer

Automated AI background replacement — upload a photo, describe the
background you want, get a composited result.

Built on a manually-debugged ComfyUI workflow (mask-based background
inpainting using BRIAAI matting for hair-accurate cutouts), with Gemini
handling two decisions that used to require manual tuning:

1. **Image analysis** — looks at edge complexity, background/studio
   contamination (fringe), and lighting, and picks pipeline parameters
   accordingly.
2. **Prompt expansion** — turns a vague background request ("rooftop")
   into a full, detailed positive/negative prompt pair, matched to the
   subject's actual lighting.

## Status
Early build — see `ANTIGRAVITY_PROMPT.md` for the full spec this repo
is being built against.

## Bring your own keys
This tool does not host inference. You provide:
- A ComfyUI endpoint (local install or a hosted instance that exposes
  the standard `/prompt`, `/history`, `/view` API)
- A Gemini API key

Neither is stored server-side beyond the lifetime of a single request.

## Repo layout
```
backend/    FastAPI orchestrator + Gemini/ComfyUI integration
frontend/   Web UI
workflows/  The exported ComfyUI workflow (API format)
```

## Known limitations
- Reflective surfaces (helmet visors, glasses) can create holes in the
  auto-generated mask — matting models weren't trained for this case.
  The analyzer flags this; manual mask touch-up may still be needed.
