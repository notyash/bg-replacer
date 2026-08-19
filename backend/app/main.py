"""
main.py

FastAPI orchestrator that ties Gemini image analysis, prompt expansion,
and ComfyUI workflow execution together into a progress-streaming pipeline.
"""

import os
import uuid
import shutil
import random
import base64
import json
import logging
from typing import Optional
from fastapi import FastAPI, File, UploadFile, Form, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
import urllib.error
from PIL import Image, ImageOps

from app.config import resolve_config, PipelineConfig
from app.analyze_image import analyze
from app.expand_prompt import expand
from app.comfyui_client import ComfyUIClient

# Configure logging to integrate with Uvicorn
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="AI Background Replacer API", version="1.0.0")

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure directories exist
os.makedirs("uploads", exist_ok=True)
os.makedirs("outputs", exist_ok=True)

# Mount static files
app.mount("/static/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static/outputs", StaticFiles(directory="outputs"), name="outputs")

# Helper to find the workflow file
def get_workflow_path() -> str:
    paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workflows", "background_replacer.json"),
        os.path.join("workflows", "background_replacer.json"),
        os.path.join("..", "workflows", "background_replacer.json"),
        "/workflows/background_replacer.json"
    ]
    for p in paths:
        if os.path.exists(p):
            logger.info(f"Found ComfyUI workflow at: {p}")
            return p
    raise FileNotFoundError("Could not locate workflows/background_replacer.json in any standard location.")

@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "AI Background Replacer is healthy."}

async def run_pipeline(
    image_path: str,
    background_description: str,
    config: PipelineConfig,
    request_base_url: str
):
    try:
        # 1. Analyze Image
        yield "data: " + json.dumps({"status": "Analyzing photo with Gemini...", "progress": 15}) + "\n\n"
        try:
            analysis = await run_in_threadpool(analyze, image_path, config.gemini_api_key)
        except Exception as e:
            logger.error(f"Gemini analysis failed: {e}")
            err_msg = str(e)
            if "api key" in err_msg.lower() or "api_key" in err_msg.lower() or "api-key" in err_msg.lower() or "invalid" in err_msg.lower():
                err_msg = "Invalid Gemini API Key or Gemini API authentication failed. Please check your credentials."
            elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                err_msg = "Gemini API Request Timed Out. Please check your internet connection or proxy/VPN settings."
            yield "data: " + json.dumps({"error": f"Gemini Analysis Failed: {err_msg}"}) + "\n\n"
            return

        warning = None
        if analysis.get("reflective_surface_detected"):
            warning = "Reflective surface detected (glasses/visor/glass). The generated mask might need manual touch-up."
            logger.warning("Reflective surface detected by Gemini.")

        # 2. Expand Prompt
        yield "data: " + json.dumps({"status": "Expanding background description with Gemini...", "progress": 35}) + "\n\n"
        lighting_fragment = analysis.get("lighting_prompt_fragment", "")
        try:
            expanded = await run_in_threadpool(expand, background_description, lighting_fragment, config.gemini_api_key)
        except Exception as e:
            logger.error(f"Gemini prompt expansion failed: {e}")
            err_msg = str(e)
            if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                err_msg = "Gemini API Request Timed Out. Please check your internet connection or proxy/VPN settings."
            yield "data: " + json.dumps({"error": f"Gemini Prompt Expansion Failed: {err_msg}"}) + "\n\n"
            return

        # 3. Connect to ComfyUI
        yield "data: " + json.dumps({"status": f"Connecting to ComfyUI ({config.comfyui_server_address})...", "progress": 50}) + "\n\n"
        client = ComfyUIClient(config.comfyui_server_address)
        
        try:
            workflow_path = get_workflow_path()
            workflow = client.load_workflow(workflow_path)
        except Exception as e:
            logger.error(f"Failed to load ComfyUI workflow: {e}")
            yield "data: " + json.dumps({"error": f"Workflow Configuration Error: {e}"}) + "\n\n"
            return

        # Build params
        # Note: Sign convention for BgGrowMask. recommended_bg_mask_grow_px is positive (3-6)
        # We negate it because negative values shrink the subject mask in the workflow.
        bg_grow_px = -abs(analysis.get("recommended_bg_mask_grow_px", 0))
        
        params = {
            "positive_prompt": expanded["positive_prompt"],
            "negative_prompt": expanded["negative_prompt"],
            "bg_grow_px": bg_grow_px,
            "bg_feather_px": analysis.get("recommended_feather_px", 0),
            "seed": random.randint(1, 10**15)
        }

        # 4. Handle upload or url mode
        if config.comfyui_mode == "upload":
            yield "data: " + json.dumps({"status": "Uploading image to ComfyUI...", "progress": 65}) + "\n\n"
            try:
                filename = await run_in_threadpool(client.upload_image, image_path)
                params["source_image_filename"] = filename
            except urllib.error.HTTPError as e:
                error_body = e.read().decode()
                logger.error(f"Failed to upload image to ComfyUI: {e.reason} - {error_body}")
                yield "data: " + json.dumps({"error": f"ComfyUI Upload Error: {e.reason}. {error_body[:200]}"}) + "\n\n"
                return
            except urllib.error.URLError as e:
                logger.error(f"Failed to upload image to ComfyUI: {e}")
                yield "data: " + json.dumps({"error": f"ComfyUI Connection Error: Could not connect to ComfyUI server at '{config.comfyui_server_address}' to upload the photo. Details: {e}"}) + "\n\n"
                return
            except Exception as e:
                logger.error(f"ComfyUI upload failed: {e}")
                yield "data: " + json.dumps({"error": f"ComfyUI Upload Failed: {e}"}) + "\n\n"
                return
        else:
            # url mode: compute the static URL serving the uploaded image
            base_filename = os.path.basename(image_path)
            public_url = f"{request_base_url}static/uploads/{base_filename}"
            params["source_image_url"] = public_url
            logger.info(f"Using URL mode. Image URL passed to ComfyUI: {public_url}")

        # Apply params
        try:
            client.apply_params(workflow, params)
        except KeyError as e:
            logger.error(f"Missing node in workflow: {e}")
            yield "data: " + json.dumps({"error": f"ComfyUI Workflow Error: {str(e)}. Please check your workflow configuration."}) + "\n\n"
            return
        except Exception as e:
            logger.error(f"Failed to apply params: {e}")
            yield "data: " + json.dumps({"error": f"ComfyUI Configuration Error: {e}"}) + "\n\n"
            return

        # 5. Queue Prompt
        yield "data: " + json.dumps({"status": "Submitting job to ComfyUI...", "progress": 75}) + "\n\n"
        try:
            prompt_id = await run_in_threadpool(client.queue_prompt, workflow)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.error(f"ComfyUI rejected prompt (400 Bad Request): {error_body}")
            err_msg = f"ComfyUI Rejected Job (HTTP 400 Bad Request): {e.reason}"
            try:
                err_json = json.loads(error_body)
                if "error" in err_json:
                    msg = err_json["error"].get("message", "")
                    details = err_json["error"].get("details", "")
                    err_msg = f"ComfyUI Error: {msg}. Details: {details}"
                elif "node_errors" in err_json:
                    node_errs = []
                    for node_id, node_err in err_json["node_errors"].items():
                        class_type = node_err.get("class_type", "")
                        errors = [err.get("message", "") for err in node_err.get("errors", [])]
                        node_errs.append(f"Node {node_id} ({class_type}): {', '.join(errors)}")
                    err_msg = f"ComfyUI Workflow Validation Failed: " + " | ".join(node_errs)
            except Exception:
                if len(error_body) < 200:
                    err_msg = f"ComfyUI Error: {error_body}"
            yield "data: " + json.dumps({"error": err_msg}) + "\n\n"
            return
        except urllib.error.URLError as e:
            logger.error(f"Failed to queue job: {e}")
            yield "data: " + json.dumps({"error": f"ComfyUI Connection Error: Could not connect to ComfyUI server at '{config.comfyui_server_address}'. Details: {e}"}) + "\n\n"
            return
        except Exception as e:
            logger.error(f"Queue prompt failed: {e}")
            yield "data: " + json.dumps({"error": f"ComfyUI Queue Job Failed: {e}"}) + "\n\n"
            return

        # 6. Wait for result
        yield "data: " + json.dumps({"status": "Generating background image in ComfyUI...", "progress": 80}) + "\n\n"
        try:
            image_refs = await run_in_threadpool(client.wait_for_result, prompt_id, "OutputImage", workflow)
        except TimeoutError as e:
            logger.error(f"ComfyUI timed out: {e}")
            yield "data: " + json.dumps({"error": "ComfyUI job execution timed out. The server took too long to complete rendering."}) + "\n\n"
            return
        except urllib.error.URLError as e:
            logger.error(f"ComfyUI disconnected during execution: {e}")
            yield "data: " + json.dumps({"error": f"ComfyUI Connection Error: Lost connection to ComfyUI server at '{config.comfyui_server_address}' during generation."}) + "\n\n"
            return
        except Exception as e:
            logger.error(f"Wait for result failed: {e}")
            yield "data: " + json.dumps({"error": f"ComfyUI Generation Failed: {e}"}) + "\n\n"
            return

        if not image_refs:
            yield "data: " + json.dumps({"error": "ComfyUI completed but returned no output image."}) + "\n\n"
            return

        # 7. Download and save result
        yield "data: " + json.dumps({"status": "Downloading output image...", "progress": 90}) + "\n\n"
        out_filename = f"output_{prompt_id}.png"
        out_path = os.path.join("outputs", out_filename)
        
        try:
            await run_in_threadpool(client.download_image, image_refs[0], out_path)
        except Exception as e:
            logger.error(f"Failed to download output image: {e}")
            yield "data: " + json.dumps({"error": f"Failed to download output image from ComfyUI: {e}"}) + "\n\n"
            return

        # Base64 encode the output
        try:
            with open(out_path, "rb") as f:
                base64_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to base64-encode output image: {e}")
            base64_data = ""

        output_url = f"{request_base_url}static/outputs/{out_filename}"

        # 8. Success
        yield "data: " + json.dumps({
            "status": "Generation complete!",
            "progress": 100,
            "result": {
                "output_url": output_url,
                "output_base64": f"data:image/png;base64,{base64_data}" if base64_data else None,
                "analysis": analysis,
                "warning": warning,
                "positive_prompt": expanded["positive_prompt"],
                "negative_prompt": expanded["negative_prompt"]
            }
        }) + "\n\n"

    except Exception as e:
        logger.error(f"Unexpected pipeline error: {e}")
        yield "data: " + json.dumps({"error": f"Unexpected backend orchestrator error: {str(e)}"}) + "\n\n"

@app.post("/api/generate")
async def generate(
    request: Request,
    file: UploadFile = File(...),
    background_description: str = Form(...),
    comfyui_server_address: Optional[str] = Form(None),
    comfyui_mode: Optional[str] = Form(None),
    gemini_api_key: Optional[str] = Form(None),
    x_comfyui_server_address: Optional[str] = Header(None),
    x_comfyui_mode: Optional[str] = Header(None),
    x_gemini_api_key: Optional[str] = Header(None),
):
    try:
        config = resolve_config(
            comfyui_server_address=comfyui_server_address,
            comfyui_mode=comfyui_mode,
            gemini_api_key=gemini_api_key,
            x_comfyui_server_address=x_comfyui_server_address,
            x_comfyui_mode=x_comfyui_mode,
            x_gemini_api_key=x_gemini_api_key
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    # Save uploaded file
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1] if file.filename else ".png"
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    
    filename = f"{file_id}{ext}"
    temp_path = os.path.join("uploads", filename)

    try:
        # Load image via PIL to transpose and resize
        img = Image.open(file.file)
        img = ImageOps.exif_transpose(img)
        
        # Downscale image if maximum dimension is larger than 1024px to prevent VRAM OOM
        max_dim = 1024
        width, height = img.size
        if width > max_dim or height > max_dim:
            if width > height:
                new_width = max_dim
                new_height = int(height * (max_dim / width))
            else:
                new_height = max_dim
                new_width = int(width * (max_dim / height))
            
            logger.info(f"Resizing uploaded image from {width}x{height} to {new_width}x{new_height}")
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
        save_format = "JPEG" if ext.lower() in (".jpg", ".jpeg") else "PNG"
        img.save(temp_path, format=save_format)
    except Exception as e:
        logger.error(f"Failed to save and process upload: {e}")
        return JSONResponse(status_code=500, content={"error": f"Failed to process and save uploaded image: {e}"})

    # Prepare base URL
    base_url = str(request.base_url)

    # Stream progress
    return StreamingResponse(
        run_pipeline(temp_path, background_description, config, base_url),
        media_type="text/event-stream"
    )

# Serve built frontend static files if dist is present (must be at the bottom)
frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "dist")
if os.path.exists(frontend_dist):
    logger.info(f"Serving built frontend from: {frontend_dist}")
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
