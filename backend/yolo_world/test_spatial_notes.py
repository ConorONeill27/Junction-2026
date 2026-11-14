"""Tests for Demo 1 persistent spatial notes, crop re-ID, 3D projection, and :8000 sync."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detection_server import DetectionServer  # noqa: E402
from spatial_notes import SpatialNoteStore  # noqa: E402


class SpatialNotesTests(unittest.TestCase):
    def test_person_a_note_reappears_for_person_b_and_syncs_to_studio(self):
        received_pins = []

        class MockStudioHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                received_pins.append(payload)
                body = json.dumps({"ok": True, "position": [1.2, 0.9, -1.4]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        studio = HTTPServer(("127.0.0.1", 0), MockStudioHandler)
        studio_thread = threading.Thread(target=studio.serve_forever, daemon=True)
        studio_thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                notes_dir = Path(tmp) / "notes"
                # Frame with a bright orange cup in the center
                frame = np.zeros((240, 320, 3), dtype=np.uint8)
                frame[:, :] = (40, 40, 40)
                cv2.rectangle(frame, (110, 80), (190, 170), (20, 130, 240), -1)

                call_count = {"n": 0}

                def fake_predict(_img):
                    call_count["n"] += 1
                    if call_count["n"] == 1:
                        return [{
                            "object_id": "person-a-cup-1",
                            "label": "cup",
                            "confidence": 0.94,
                            "bbox_xyxy": [110.0, 80.0, 190.0, 170.0],
                        }]
                    # Person B walks in from a different angle: shifted box and brand-new object_id!
                    return [{
                        "object_id": "person-b-cup-99",
                        "label": "cup",
                        "confidence": 0.91,
                        "bbox_xyxy": [140.0, 90.0, 220.0, 180.0],
                    }]

                server = DetectionServer("127.0.0.1", 0, predict=fake_predict, notes_dir=notes_dir)
                server.notes = SpatialNoteStore(notes_dir, studio_url=f"http://127.0.0.1:{studio.server_address[1]}")
                server.start()
                port = server.http.server_address[1]
                try:
                    # 1. Person A sees the cup
                    ok, jpg = cv2.imencode(".jpg", frame)
                    self.assertTrue(ok)
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/frame",
                        data=jpg.tobytes(),
                        headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        d1 = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(d1["detections"][0]["object_id"], "person-a-cup-1")

                    # 2. Person A leaves a note
                    note_req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/notes",
                        data=json.dumps({
                            "object_id": "person-a-cup-1",
                            "text": "Don't drink this, it's cold!",
                            "author": "Person A",
                        }).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(note_req, timeout=5) as resp:
                        saved = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(saved["ok"])
                    self.assertEqual(saved["note"]["position_3d"], [1.2, 0.9, -1.4])
                    self.assertEqual(len(received_pins), 1)
                    self.assertEqual(received_pins[0]["text"], "Don't drink this, it's cold!")

                    # 3. Person B walks in (shifted frame, different object_id) -> note re-appears!
                    frame_b = np.zeros((240, 320, 3), dtype=np.uint8)
                    frame_b[:, :] = (40, 40, 40)
                    cv2.rectangle(frame_b, (140, 90), (220, 180), (20, 130, 240), -1)
                    ok_b, jpg_b = cv2.imencode(".jpg", frame_b)
                    self.assertTrue(ok_b)
                    req_b = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/frame",
                        data=jpg_b.tobytes(),
                        headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_b, timeout=5) as resp:
                        d2 = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(d2["detections"][0]["object_id"], "person-b-cup-99")
                    self.assertEqual(d2["detections"][0]["note"], "Don't drink this, it's cold!")

                    # 4. Clear notes (privacy reset)
                    del_req = urllib.request.Request(f"http://127.0.0.1:{port}/api/notes", method="DELETE")
                    with urllib.request.urlopen(del_req, timeout=5) as resp:
                        cleared = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(cleared["notes"], [])
                finally:
                    server.close()
        finally:
            studio.shutdown()
            studio.server_close()

    def test_3d_projection_emits_note_when_yolo_misses_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SpatialNoteStore(Path(tmp) / "notes", studio_url="")
            store.add_note(
                label="whiteboard",
                text="Standup at 10am",
                position_3d=[0.0, 0.0, 2.0],
                sync_studio=False,
            )
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            dets = store.annotate_detections([], frame, cam_pose=(np.array([0.0, 0.0, 0.0]), np.eye(3)))
            self.assertEqual(len(dets), 1)
            self.assertEqual(dets[0]["note"], "Standup at 10am")
            self.assertEqual(dets[0]["anchor_source"], "3d_projection")


if __name__ == "__main__":
    unittest.main()
