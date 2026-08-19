"""
config.py

Handles per-request settings resolution for Bring-Your-Own-Key (BYOK) paradigm.
Ensures user API keys are never persisted server-side.
"""

import os
from pydantic import BaseModel
from typing import Optional

class PipelineConfig(BaseModel):
    comfyui_server_address: str
    comfyui_mode: str  # "url" or "upload"
    gemini_api_key: str

def resolve_config(
    comfyui_server_address: Optional[str] = None,
    comfyui_mode: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
    x_comfyui_server_address: Optional[str] = None,
    x_comfyui_mode: Optional[str] = None,
    x_gemini_api_key: Optional[str] = None,
) -> PipelineConfig:
    """
    Resolve configuration settings for a request.
    Priority order:
    1. Request form field / payload
    2. Request headers
    3. Server environmental variables
    """
    # 1. ComfyUI Server Address
    server_addr = comfyui_server_address
    if not server_addr and x_comfyui_server_address:
        server_addr = x_comfyui_server_address
    if not server_addr:
        server_addr = os.environ.get("COMFYUI_SERVER_ADDRESS", "127.0.0.1:8188")

    # 2. ComfyUI Mode
    mode = comfyui_mode
    if not mode and x_comfyui_mode:
        mode = x_comfyui_mode
    if not mode:
        mode = os.environ.get("COMFYUI_MODE", "upload")
    
    if mode not in ("upload", "url"):
        mode = "upload"

    # 3. Gemini API Key
    api_key = gemini_api_key
    if not api_key and x_gemini_api_key:
        api_key = x_gemini_api_key
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise ValueError(
            "Gemini API key is missing. Please provide it in the 'gemini_api_key' form field, "
            "the 'X-Gemini-API-Key' header, or set the GEMINI_API_KEY environment variable on the server."
        )

    return PipelineConfig(
        comfyui_server_address=server_addr,
        comfyui_mode=mode,
        gemini_api_key=api_key
    )
