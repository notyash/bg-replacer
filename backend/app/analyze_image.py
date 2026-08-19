"""
analyze_image.py

Sends the user's uploaded photo to Gemini and asks it to act as the
"manual judgment" step we did by hand during development:
  - is there fine hair/fur detail that needs heavy feathering?
  - is there visible background contamination / fringe on the subject
    (e.g. a studio white-backdrop photo) that needs mask-shrinking?
  - what's the subject's apparent lighting direction/quality, so we can
    steer the new background's lighting to match?

Returns a structured dict of pipeline parameters that main.py feeds
directly into the ComfyUI workflow JSON.
"""

import os
import json
import google.generativeai as genai

GEMINI_MODEL = "gemini-2.0-flash"  # fast + free-tier friendly; swap if needed

ANALYSIS_PROMPT = """
You are configuring an AI background-replacement pipeline (ComfyUI).
Look at the attached photo of a person and answer ONLY with a single
JSON object (no markdown fences, no commentary) with exactly these
fields:

{
  "edge_complexity": "low" | "medium" | "high",
    // "high" if there is visible fine/wispy hair, flyaway strands, or
    // fur. "low" if the subject's outline is mostly clean fabric/skin
    // with simple contours.

  "background_contamination": true | false,
    // true if this looks like a studio/product photo shot on a flat
    // seamless backdrop (white, gray, colored paper) where the
    // backdrop's light is likely bleeding onto the subject's edge
    // pixels (rim light / color spill). false for photos already
    // shot in a real environment (outdoors, room, car, etc).

  "lighting_direction": "front" | "side" | "back" | "top" | "diffuse",
    // dominant apparent light direction on the subject's face/body.
    // "diffuse" if lighting is soft/even with no strong directionality
    // (e.g. overcast, softbox studio lighting).

  "lighting_quality": "soft" | "hard",
    // "hard" if shadows have crisp edges and there's strong contrast
    // (direct sun, bare flash). "soft" if shadows are gradual/diffuse.

  "recommended_feather_px": <integer>,
    // 2-4 for low edge_complexity, 8-15 for high edge_complexity.

  "recommended_bg_mask_grow_px": <integer>,
    // 0 if background_contamination is false.
    // 3-6 if background_contamination is true (shrinks the kept-subject
    // region so contaminated edge pixels fall into the regenerate zone).

  "lighting_prompt_fragment": "<short phrase>"
    // A short natural-language phrase describing lighting that a new
    // background should have to plausibly match this subject's
    // existing lighting. E.g. "soft diffused overcast light, no harsh
    // directional shadows" or "warm side-lit golden hour glow".
}

Be decisive - pick the single best-fitting value for each field, do not
hedge or return multiple options.
"""


def analyze(image_path: str, api_key: str | None = None) -> dict:
    """Run Gemini analysis on the uploaded photo and return pipeline params."""
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY env var or pass api_key=")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    mime_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

    response = model.generate_content(
        [
            ANALYSIS_PROMPT,
            {"mime_type": mime_type, "data": image_bytes},
        ],
        generation_config={"temperature": 0.2},
    )

    raw = response.text.strip()
    # Gemini sometimes wraps JSON in ```json fences even when asked not to
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        params = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return valid JSON:\n{raw}") from e

    return params


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python analyze_image.py <path_to_image>")
        sys.exit(1)

    result = analyze(sys.argv[1])
    print(json.dumps(result, indent=2))
