"""
comfyui_client.py

Loads an exported ComfyUI "API format" workflow JSON (from SeaArt's
Export (API) option, or ComfyUI's own "Save (API Format)"), edits
specific node inputs by their TITLE (set via _meta.title, which you
assign by renaming nodes in the graph before export), submits the job
over the ComfyUI HTTP API, polls until done, and downloads the result.

Works identically against a local ComfyUI instance or any hosted
instance that exposes the same /prompt, /history, /view endpoints.
"""

import json
import time
import uuid
import urllib.request
import urllib.parse
import os


class ComfyUIClient:
    def __init__(self, server_address: str):
        """
        server_address: host:port with no scheme, e.g. "127.0.0.1:8188"
        or the host:port SeaArt/your host gives you for API access.
        """
        self.server_address = server_address
        self.client_id = str(uuid.uuid4())

    # ---------- workflow editing ----------

    def load_workflow(self, path: str) -> dict:
        with open(path, "r") as f:
            return json.load(f)

    def _find_node_by_title(self, workflow: dict, title: str) -> str:
        for node_id, node in workflow.items():
            if node.get("_meta", {}).get("title") == title:
                return node_id
        raise KeyError(
            f"No node titled '{title}' found in workflow. "
            f"Rename the node in ComfyUI/SeaArt and re-export."
        )

    def set_text(self, workflow: dict, title: str, text: str) -> None:
        node_id = self._find_node_by_title(workflow, title)
        workflow[node_id]["inputs"]["text"] = text

    def set_widget_value(self, workflow: dict, title: str, field: str, value) -> None:
        node_id = self._find_node_by_title(workflow, title)
        workflow[node_id]["inputs"][field] = value

    def set_image_upload(self, workflow: dict, title: str, filename: str) -> None:
        """Point a LoadImage-style node at an already-uploaded filename."""
        node_id = self._find_node_by_title(workflow, title)
        workflow[node_id]["inputs"]["image"] = filename

    def apply_params(self, workflow: dict, params: dict) -> dict:
        """
        params keys expected (all optional - only sets what's provided):
          positive_prompt, negative_prompt,
          bg_grow_px, bg_feather_px,
          seed, source_image_filename, source_image_url

        Node titles below match the actual exported background_replacer.json
        (note: seed lives directly on KSampler - there's no separate seed
        node in this graph; prompt nodes are titled with a space).
        """
        if "positive_prompt" in params:
            self.set_text(workflow, "Positive Prompt", params["positive_prompt"])
        if "negative_prompt" in params:
            self.set_text(workflow, "Negative Prompt", params["negative_prompt"])
        if "bg_grow_px" in params:
            # note: this workflow uses a NEGATIVE expand value on BgGrowMask
            # (it grows the SUBJECT mask before invert, e.g. -2) - see the
            # "fringe fix" logic worked out during manual testing. Pass a
            # negative number here to shrink the kept-subject region.
            self.set_widget_value(workflow, "BgGrowMask", "expand", params["bg_grow_px"])
        if "bg_feather_px" in params:
            for side in ("left", "top", "right", "bottom"):
                self.set_widget_value(workflow, "BgFeatherMask", side, params["bg_feather_px"])
        if "seed" in params:
            self.set_widget_value(workflow, "KSampler", "seed", params["seed"])
        if "source_image_filename" in params:
            self.set_image_upload(workflow, "Load Image", params["source_image_filename"])
        if "source_image_url" in params:
            # SeaArt's LoadImage accepts a direct URL string in the "image"
            # field (confirmed from the exported workflow). For a local
            # ComfyUI instance this will NOT work - upload the file first
            # via upload_image() and use source_image_filename instead.
            self.set_image_upload(workflow, "Load Image", params["source_image_url"])
        return workflow

    # ---------- HTTP / API ----------

    def upload_image(self, image_path: str) -> str:
        """Upload a local image file to ComfyUI's input folder, return its filename."""
        with open(image_path, "rb") as f:
            data = f.read()

        boundary = uuid.uuid4().hex
        filename = os.path.basename(image_path)

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"http://{self.server_address}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        return result["name"]

    def queue_prompt(self, workflow: dict) -> str:
        payload = json.dumps({"prompt": workflow, "client_id": self.client_id}).encode()
        req = urllib.request.Request(
            f"http://{self.server_address}/prompt",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        return result["prompt_id"]

    def wait_for_result(self, prompt_id: str, output_node_title: str, workflow: dict,
                         poll_interval: float = 2.0, timeout: float = 300.0) -> list[dict]:
        """Poll /history until the job finishes, return output image refs."""
        output_node_id = self._find_node_by_title(workflow, output_node_title)
        start = time.time()

        while time.time() - start < timeout:
            with urllib.request.urlopen(
                f"http://{self.server_address}/history/{prompt_id}"
            ) as resp:
                history = json.loads(resp.read())

            if prompt_id in history:
                outputs = history[prompt_id]["outputs"]
                if output_node_id in outputs and "images" in outputs[output_node_id]:
                    return outputs[output_node_id]["images"]

            time.sleep(poll_interval)

        raise TimeoutError(f"Job {prompt_id} did not finish within {timeout}s")

    def download_image(self, image_ref: dict, save_path: str) -> None:
        params = urllib.parse.urlencode({
            "filename": image_ref["filename"],
            "subfolder": image_ref.get("subfolder", ""),
            "type": image_ref.get("type", "output"),
        })
        with urllib.request.urlopen(
            f"http://{self.server_address}/view?{params}"
        ) as resp:
            data = resp.read()
        with open(save_path, "wb") as f:
            f.write(data)
