"""Unit tests for backend video_localizer raycasting & 3D localization."""

from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_localizer import _estimate_pose_and_raycast  # noqa: E402


class VideoLocalizerTests(unittest.TestCase):
    def test_raycast_recovers_floor_region_color_matched(self):
        # Build a synthetic room corner: dark wall on left (-X), bright white bed on right (+X), floor at y=0
        rng = np.random.default_rng(7)
        pts = []
        # Floor tiles around z in [-2.0, -0.5], x in [-1.0, 1.0], y=0.05
        for _ in range(4000):
            x = rng.uniform(-1.2, 1.2)
            z = rng.uniform(-2.2, -0.5)
            # Left dark area vs right white bed/floor
            if x > 0.2 and z > -1.8:
                pts.append([x, 0.25, z, 0.92, 0.92, 0.95])
            elif x < -0.2:
                pts.append([x, rng.uniform(0.05, 0.9), z, 0.15, 0.15, 0.18])
            else:
                pts.append([x, 0.05, z, 0.65, 0.58, 0.50])
        arr = np.asarray(pts, dtype=np.float32)
        xyz = arr[:, :3]
        rgb = np.clip(arr[:, 3:6] * 255.0, 0, 255).astype(np.uint8)

        # Synthetic query image looking toward -Z (yaw ~ 0): dark left, bright white right
        bgr = np.full((320, 180, 3), 140, dtype=np.uint8)
        bgr[:, :70] = (45, 40, 40)
        bgr[:, 110:] = (240, 235, 235)
        cam_pos = np.array([0.0, 0.7, 0.4], dtype=np.float32)

        hit, yaw, pitch = _estimate_pose_and_raycast(bgr, cam_pos, xyz, rgb, (0.45, 0.60, 0.55, 0.70))
        self.assertEqual(len(hit), 3)
        self.assertTrue(hit[2] < 0.0, f"Expected hit forward in -Z, got {hit} (yaw={yaw}, pitch={pitch})")


if __name__ == "__main__":
    unittest.main()
