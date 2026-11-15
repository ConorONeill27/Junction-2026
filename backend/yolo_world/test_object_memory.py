"""Tests for Demo 3 Passive Object Memory (2-room bottle finder + privacy controls)."""

import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

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
                studio_url="",
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
                studio_url="",
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

    def test_multi_instance_bottles_privacy_block_and_session_wipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            dets = [{"object_id": "b1", "label": "bottle", "confidence": 0.92, "bbox_xyxy": [40.0, 30.0, 120.0, 190.0]}]
            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=lambda _f: dets,
                notes_dir=Path(tmp) / "notes",
                places_dir=Path(tmp) / "places",
                memory_dir=Path(tmp) / "memory",
                studio_url="",
            ).start()
            port = server.http.server_address[1]
            try:
                # Blue bottle frame
                f1 = np.full((240, 320, 3), 20, dtype=np.uint8)
                f1[30:190, 40:120] = (230, 40, 20)
                _, j1 = cv2.imencode(".jpg", f1)
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame", data=j1.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"}, method="POST",
                ), timeout=5).read()

                # Distinct red bottle frame
                f2 = np.full((240, 320, 3), 20, dtype=np.uint8)
                f2[30:190, 40:120] = (20, 40, 230)
                _, j2 = cv2.imencode(".jpg", f2)
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame", data=j2.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"}, method="POST",
                ), timeout=5).read()

                found = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/memory/find?q=bottle", timeout=5,
                ).read().decode("utf-8"))
                self.assertTrue(found["found"])
                self.assertEqual(found["instance_count"], 2)

                # Privacy pause clears latest_frame and skips /api/frame inference
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/privacy",
                    data=json.dumps({"paused": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                ), timeout=5).read()
                self.assertIsNone(server.latest_frame)
                res_paused = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame", data=j1.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"}, method="POST",
                ), timeout=5).read().decode("utf-8"))
                self.assertTrue(res_paused["paused"])
                self.assertIsNone(server.latest_frame)

                # Full session wipe
                wiped = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/session", method="DELETE",
                ), timeout=5).read().decode("utf-8"))
                self.assertTrue(wiped["cleared"])
                mem = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/memory", timeout=5).read().decode("utf-8"))
                self.assertEqual(mem["memories"], [])
            finally:
                server.close()

    def test_pause_during_in_flight_inference_does_not_restore_frame_or_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = threading.Event()
            release = threading.Event()
            dets = [{"object_id": "b1", "label": "bottle", "confidence": 0.95, "bbox_xyxy": [20.0, 20.0, 100.0, 180.0]}]

            def slow_predict(_frame):
                started.set()
                release.wait(timeout=3.0)
                return dets

            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=slow_predict,
                notes_dir=Path(tmp) / "notes",
                places_dir=Path(tmp) / "places",
                memory_dir=Path(tmp) / "memory",
                studio_url="",
            ).start()
            port = server.http.server_address[1]
            try:
                frame = np.full((240, 320, 3), 50, dtype=np.uint8)
                ok, jpg = cv2.imencode(".jpg", frame)
                self.assertTrue(ok)
                resp_box = {}

                def send_frame():
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/frame",
                        data=jpg.tobytes(),
                        headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        resp_box["body"] = json.loads(resp.read().decode("utf-8"))

                t = threading.Thread(target=send_frame)
                t.start()
                self.assertTrue(started.wait(timeout=2.0))

                # Toggle Privacy Pause while inference is in flight
                with urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/privacy",
                    data=json.dumps({"paused": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ), timeout=5) as resp:
                    self.assertTrue(json.loads(resp.read().decode("utf-8"))["paused"])

                release.set()
                t.join(timeout=3.0)

                self.assertTrue(resp_box.get("body", {}).get("paused"))
                self.assertEqual(resp_box.get("body", {}).get("detections"), [])
                self.assertIsNone(server.latest_frame)
                self.assertEqual(server.latest_detections, [])
                self.assertEqual(server.snapshot.get("detections"), [])
                self.assertEqual(server.memory.list_memories(), [])
            finally:
                release.set()
                server.close()

    def test_api_token_and_studio_session_wipe_propagation(self):
        studio_calls = []

        class MockStudio(BaseHTTPRequestHandler):
            def do_DELETE(self):
                studio_calls.append((self.path, self.headers.get("X-WorldGit-Token")))
                body = json.dumps({"ok": True, "cleared": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        mock = HTTPServer(("127.0.0.1", 0), MockStudio)
        mock_thread = threading.Thread(target=mock.serve_forever, daemon=True)
        mock_thread.start()
        with tempfile.TemporaryDirectory() as tmp:
            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=lambda _f: [],
                notes_dir=Path(tmp) / "notes",
                places_dir=Path(tmp) / "places",
                memory_dir=Path(tmp) / "memory",
                studio_url=f"http://127.0.0.1:{mock.server_address[1]}",
                api_token="demo-secret",
            ).start()
            port = server.http.server_address[1]
            try:
                with self.assertRaises(urllib.error.HTTPError) as unauth:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/memory", timeout=5)
                self.assertEqual(unauth.exception.code, 401)

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/session",
                    headers={"X-WorldGit-Token": "demo-secret"},
                    method="DELETE",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    wiped = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(wiped["cleared"])
                self.assertTrue(wiped["studio_cleared"])
                self.assertEqual(studio_calls, [("/api/session", "demo-secret")])
            finally:
                server.close()
                mock.shutdown()
                mock.server_close()


if __name__ == "__main__":
    unittest.main()
