"""Tests for Demo 2 Visual Place Anchors (restaurant reviews while walking past)."""

import json
import sys
from pathlib import Path
import tempfile
import unittest
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detection_server import DetectionServer  # noqa: E402
from seed_demo_places import make_storefront_card, seed_places  # noqa: E402


class PlaceAnchorsTests(unittest.TestCase):
    def test_walking_past_seeded_restaurant_emits_review_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            places_dir = Path(tmp) / "places"
            seeded = seed_places(places_dir, reset=True)
            self.assertEqual(len(seeded), 4)

            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=lambda _frame: [],
                notes_dir=Path(tmp) / "notes",
                places_dir=places_dir,
            ).start()
            port = server.http.server_address[1]
            try:
                # Simulate walking past Burger King in a wider mall corridor frame (shifted & scaled)
                card = make_storefront_card("Burger King", (20, 60, 210))
                scene = np.full((480, 640, 3), 25, dtype=np.uint8)
                scaled = cv2.resize(card, (288, 192))
                scene[120:312, 180:468] = scaled

                ok, jpg = cv2.imencode(".jpg", scene)
                self.assertTrue(ok)
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame",
                    data=jpg.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))

                place_hits = [d for d in payload["detections"] if d.get("anchor_source") == "place_anchor"]
                self.assertEqual(len(place_hits), 1)
                self.assertIn("Burger King (1/5)", place_hits[0]["label"])
                self.assertIn("Worst Burger King ever", place_hits[0]["note"])

                # Verify GET /api/places and DELETE /api/places
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/places", timeout=5) as resp:
                    listed = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(listed["places"]), 4)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
