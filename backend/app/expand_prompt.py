"""
expand_prompt.py

Takes the user's short/vague background request (e.g. "rooftop",
"cozy cafe", "sunset beach") plus the lighting fragment from
analyze_image.py, and asks Gemini to expand it into a full positive
and negative prompt pair for the ComfyUI inpainting KSampler -
encoding everything we learned by hand tonight:
  - explicitly state the scene is EMPTY (no people) to fight the
    checkpoint's bias toward inserting a second person
  - bake in the subject's actual lighting so the new background
    doesn't visually contradict them
  - carry a standard negative-prompt baseline (extra people, straps/
    bags/objects, artifacts) every time, since these were recurring
    failure modes in testing
"""

import os
import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

BASE_NEGATIVE = (
    "person, people, human, man, woman, figure, silhouette, face, "
    "extra person, second person, hands, arm, body, crowd, tourist, "
    "bag, strap, pouch, backpack, object, accessory, "
    "blurry, low quality, distorted, oversaturated, artifacts"
)

EXPANSION_PROMPT_TEMPLATE = """
You are writing a Stable Diffusion inpainting prompt for a
background-replacement tool. The subject of the photo will stay
completely untouched (masked out) - you are ONLY describing the new
background scene that fills the space around them.

User's requested background (may be vague): "{user_request}"
Subject's existing lighting (must be visually matched): "{lighting_fragment}"

Write a JSON object with exactly these fields, no markdown fences,
no commentary:

{{
  "positive_prompt": "<comma-separated descriptive tags/phrases>",
  "negative_prompt_additions": "<comma-separated extra negative terms
      specific to this scene, e.g. if the scene could plausibly
      contain animals/vehicles/other objects the user didn't ask for>"
}}

Rules for positive_prompt:
- Expand the vague request into a specific, vivid, photorealistic
  scene description (concrete details: time of day, materials,
  weather, mood).
- Explicitly include language establishing the scene is EMPTY of
  people (e.g. "empty", "no people", "deserted", "unoccupied") -
  this is critical, always include it regardless of the request.
- Include the given lighting fragment naturally so the generated
  scene's lighting matches the subject.
- Include "photorealistic, high detail" at the end.
- Do NOT mention the subject/person at all - only the environment.

Rules for negative_prompt_additions:
- Only include terms relevant to THIS specific scene that aren't
  already covered by a generic baseline negative prompt (which
  already blocks: extra people, bags/straps, blurriness, artifacts).
  Example: a "rooftop" scene might add "railing collapse, unsafe
  structure"; an "empty beach" might add "boats, umbrellas, footprints".
  Return an empty string if nothing scene-specific comes to mind.
"""


def expand(user_request: str, lighting_fragment: str, api_key: str | None = None) -> dict:
    """Expand a vague background request into full positive/negative prompts."""
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY env var or pass api_key=")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=60_000)
    )

    prompt = EXPANSION_PROMPT_TEMPLATE.format(
        user_request=user_request,
        lighting_fragment=lighting_fragment,
    )

    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.7),
            )
            break
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            err_msg = str(e).lower()
            if any(x in err_msg for x in ["504", "503", "502", "429", "deadline", "timeout", "timed out", "read operation timed out"]):
                sleep_time = 2 * (attempt + 1)
                logger.warning(
                    f"Gemini API warning: request failed on attempt {attempt+1} due to transient error: {e}. "
                    f"Retrying in {sleep_time}s..."
                )
                time.sleep(sleep_time)
            else:
                raise e

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini did not return valid JSON:\n{raw}") from e

    full_negative = BASE_NEGATIVE
    extra = result.get("negative_prompt_additions", "").strip()
    if extra:
        full_negative = f"{BASE_NEGATIVE}, {extra}"

    return {
        "positive_prompt": result["positive_prompt"],
        "negative_prompt": full_negative,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print('Usage: python expand_prompt.py "<user request>" "<lighting fragment>"')
        sys.exit(1)

    result = expand(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2))
