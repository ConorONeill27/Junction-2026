"""Tests for Demo 3 Passive Object Memory (2-room bottle finder + privacy controls)."""

import json
import sys
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detection_server import DetectionServer  # noqa: E402


class ObjectMemoryTests(unittest.TestCase):
    def test_bottle_memory_and_privacy_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            room1_dets = [
                {"object_id": "b1", "label": "bottle", "confidence": 0.92, "bbox_xyxy": [100.0, 90.0, 145.0, 210.0]},
                {"object_id": "l1", "label": "laptop", "confidence": 0.89, "bbox_xyxy": [170.0, 120.0, 290.0, 220.0]},
            ]
            phase = {"room": 1}

            def fake_predict(_frame):
                return room1_dets if phase["room"] == 1 else []

            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=fake_predict,
                notes_dir=Path(tmp) / "notes",
                places_dir=Path(tmp) / "places",
                memory_dir=Path(tmp) / "memory",
            ).start()
            port = server.http.server_address[1]
            try:
                frame = np.full((240, 320, 3), 35, dtype=np.uint8)
                ok, jpg = cv2.imencode(".jpg", frame)
                self.assertTrue(ok)

                # 1. Room 1: Bottle seen near laptop
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame",
                    data=jpg.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                # 2. Room 2: Bottle is no longer in view, ask "Where is my bottle?"
                phase["room"] = 2
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/memory/find?q=bottle", timeout=5) as resp:
                    found = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(found["found"])
                self.assertIn("near laptop", found["summary"])
                self.assertTrue(len(found.get("snapshot_b64", "")) > 100)

                # 3. Privacy pause prevents recording new objects
                pause_req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/privacy",
                    data=json.dumps({"paused": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(pause_req, timeout=5) as resp:
                    self.assertTrue(json.loads(resp.read().decode("utf-8"))["paused"])

                # 4. Clear all memories
                del_req = urllib.request.Request(f"http://127.0.0.1:{port}/api/memory", method="DELETE")
                with urllib.request.urlopen(del_req, timeout=5) as resp:
                    self.assertEqual(json.loads(resp.read().decode("utf-8"))["memories"], [])

                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/memory/find?q=bottle", timeout=5)
                self.assertEqual(ctx.exception.code, 404)
            finally:
                server.close()

    def test_left_behind_voice_query_beside_left_bed(self):
        with tempfile.TemporaryDirectory() as tmp:
            room_dets = [
                {"object_id": "c1", "label": "chair", "confidence": 0.70, "bbox_xyxy": [0.0, 250.0, 345.0, 720.0]},
                {"object_id": "b1", "label": "bottle", "confidence": 0.49, "bbox_xyxy": [525.0, 600.0, 583.0, 710.0]},
                {"object_id": "bed1", "label": "bed", "confidence": 0.51, "bbox_xyxy": [598.0, 380.0, 720.0, 1000.0]},
            ]
            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=lambda _f: room_dets,
                notes_dir=Path(tmp) / "notes",
                places_dir=Path(tmp) / "places",
                memory_dir=Path(tmp) / "memory",
            ).start()
            port = server.http.server_address[1]
            try:
                frame = np.full((1000, 720, 3), 40, dtype=np.uint8)
                ok, jpg = cv2.imencode(".jpg", frame)
                self.assertTrue(ok)
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame",
                    data=jpg.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                ask_req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/memory/ask",
                    data=json.dumps({"question": "hey meta, have i left anything behind?"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(ask_req, timeout=5) as resp:
                    ans = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(ans["found"])
                self.assertEqual(
                    ans["spoken_response"],
                    "You left your water bottle on the ground beside the left bed.",
                )
                self.assertTrue(len(ans.get("snapshot_b64", "")) > 100)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
