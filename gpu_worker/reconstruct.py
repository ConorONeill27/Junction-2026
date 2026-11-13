"""
Modal GPU Worker for WorldGit 3D Reconstruction (MASt3R-SLAM / Depth-to-PointCloud).
Deploy with: modal deploy gpu_worker/reconstruct.py
"""
import base64
import io
from typing import Any, Dict, List

import modal

app = modal.App("worldgit-3d-worker")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "torchvision", "transformers", "pillow", "numpy", "fastapi[standard]")
)


@app.cls(gpu="L4", image=image, scaledown_window=120)
class SpatialReconstructor:
    @modal.enter()
    def load_model(self) -> None:
        import torch
        from transformers import pipeline

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # Fast monocular depth estimator for real-time frame-to-3D point projection
        self.depth_pipe = pipeline(
            task="depth-estimation",
            model="depth-anything/Depth-Anything-V2-Small-hf",
            device=0 if self.device == "cuda" else -1,
        )

    @modal.fastapi_endpoint(method="POST")
    def reconstruct_frame(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Accepts base64 RGB frame + wearer pose, returns downsampled 3D colored points."""
        import numpy as np
        from PIL import Image

        img_bytes = base64.b64decode(payload["image_b64"])
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((320, 240))
        depth_out = self.depth_pipe(img)["depth"]
        depth = np.array(depth_out, dtype=np.float32)
        depth = np.clip(depth / 255.0 * 6.0, 0.3, 6.0)  # normalized metric proxy

        rgb = np.array(img, dtype=np.float32) / 255.0
        pose = payload.get("pose", [0.0, 1.5, 0.0])
        yaw = float(payload.get("yaw", 0.0))

        # Backproject subsampled grid into world coordinates
        fx, fy = 260.0, 260.0
        cx, cy = 160.0, 120.0
        step = 6
        cos_y, sin_y = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))

        points: List[List[float]] = []
        for v in range(0, 240, step):
            for u in range(0, 320, step):
                z_cam = float(depth[v, u])
                x_cam = (u - cx) * z_cam / fx
                y_cam = -(v - cy) * z_cam / fy

                xw = pose[0] + x_cam * cos_y + z_cam * sin_y
                yw = pose[1] + y_cam
                zw = pose[2] - x_cam * sin_y + z_cam * cos_y
                r, g, b = rgb[v, u]
                points.append([round(xw, 3), round(yw, 3), round(zw, 3), round(float(r), 2), round(float(g), 2), round(float(b), 2)])

        return {"point_count": len(points), "points": points}
