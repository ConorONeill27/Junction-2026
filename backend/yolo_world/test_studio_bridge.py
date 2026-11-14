import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detection_server import DetectionServer
from object_memory import ObjectMemoryStore
from place_anchors import PlaceAnchorStore
from spatial_notes import SpatialNoteStore


class _MockStudioHandler(BaseHTTPRequestHandler):
    received = []
    lock = threading.Lock()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        with self.lock:
            self.received.append((self.path, body))
        resp = json.dumps({"ok": True, "position": [1.0, 0.5, -1.0]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


class StudioBridgeTests(unittest.TestCase):
    def setUp(self):
        _MockStudioHandler.received.clear()
        self.mock_server = HTTPServer(("127.0.0.1", 0), _MockStudioHandler)
        self.port = self.mock_server.server_address[1]
        self.thread = threading.Thread(target=self.mock_server.serve_forever, daemon=True)
        self.thread.start()
        self.studio_url = f"http://127.0.0.1:{self.port}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.mock_server.shutdown()
        self.mock_server.server_close()
        self.tmp.cleanup()

    def _wait_for_path(self, path, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with _MockStudioHandler.lock:
                for p, body in _MockStudioHandler.received:
                    if p == path:
                        return body
            time.sleep(0.03)
        return None

    def test_place_anchor_forwards_scoped_review_to_studio(self):
        store = PlaceAnchorStore(Path(self.tmp.name) / "places", studio_url=self.studio_url)
        rec = store.add_place(
            name="Subway",
            rating=5,
            review="Great turkey sub",
            author="Alex",
            audience_type="group",
            group_ids=["grp_foodies"],
            lat=53.3498,
            lon=-6.2603,
        )
        self.assertEqual(rec["audience_type"], "group")
        self.assertEqual(rec["group_ids"], ["grp_foodies"])
        forwarded = self._wait_for_path("/api/pin")
        self.assertIsNotNone(forwarded)
        self.assertEqual(forwarded["pin_type"], "place_review")
        self.assertEqual(forwarded["place_name"], "Subway")
        self.assertEqual(forwarded["audience_type"], "group")
        self.assertEqual(forwarded["group_ids"], ["grp_foodies"])
        self.assertAlmostEqual(forwarded["lat"], 53.3498, places=4)

    def test_spatial_note_store_carries_audience_scope(self):
        notes = SpatialNoteStore(Path(self.tmp.name) / "notes", studio_url=self.studio_url)
        rec = notes.add_note(
            label="bottle",
            text="Drink more water",
            author="Conor",
            audience_type="friends",
            group_ids=[],
            target_user_ids=["usr_mum"],
        )
        self.assertEqual(rec["audience_type"], "friends")
        self.assertEqual(rec["target_user_ids"], ["usr_mum"])
        forwarded = self._wait_for_path("/api/pin-from-hud")
        self.assertIsNotNone(forwarded)
        self.assertEqual(forwarded["audience_type"], "friends")
        self.assertEqual(forwarded["target_user_ids"], ["usr_mum"])

    def test_object_memory_forwards_sighting_to_studio(self):
        import numpy as np

        mem = ObjectMemoryStore(Path(self.tmp.name) / "mem", save_interval_s=0.0, studio_url=self.studio_url)
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        mem.observe([{"label": "bottle", "confidence": 0.91, "bbox_xyxy": [40, 20, 90, 100]}], frame)
        forwarded = self._wait_for_path("/api/objects/sighting")
        self.assertIsNotNone(forwarded)
        self.assertEqual(forwarded["label"], "bottle")
        self.assertEqual(forwarded["world_id"], "world_1")

    def test_detection_server_accepts_studio_url(self):
        srv = DetectionServer(
            port=0,
            notes_dir=Path(self.tmp.name) / "n",
            places_dir=Path(self.tmp.name) / "p",
            memory_dir=Path(self.tmp.name) / "m",
            studio_url=self.studio_url,
        ).start()
        try:
            self.assertEqual(srv.places.studio_url, self.studio_url)
            self.assertEqual(srv.memory.studio_url, self.studio_url)
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
