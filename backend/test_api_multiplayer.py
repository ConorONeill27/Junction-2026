import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from db import SpatialDatabase
from social_store import seed_everyday_social_data
import main as backend_main


class MultiplayerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.orig_db = backend_main.world.db
        cls.orig_objects = dict(backend_main.world.objects)
        cls.orig_pins = dict(backend_main.world.pins)
        backend_main.world.objects.clear()
        backend_main.world.pins.clear()
        cls.test_db = SpatialDatabase(sqlite_path=Path(cls.tmp.name) / "test_api.db")
        seed_everyday_social_data(cls.test_db)
        backend_main.world.db = cls.test_db
        cls.client = TestClient(backend_main.app)

    @classmethod
    def tearDownClass(cls):
        backend_main.world.db = cls.orig_db
        backend_main.world.objects.clear()
        backend_main.world.objects.update(cls.orig_objects)
        backend_main.world.pins.clear()
        backend_main.world.pins.update(cls.orig_pins)
        cls.test_db.close()
        cls.tmp.cleanup()

    def test_scoped_pin_filtering_by_viewer(self):
        res = self.client.post(
            "/api/pin",
            json={
                "author": "Alec",
                "author_id": "usr-alec",
                "text": "Room keycard is on the desk",
                "pin_type": "note",
                "position": [0.2, 0.9, -0.4],
                "landmark": "Hotel Desk",
                "audience_type": "group",
                "group_ids": ["grp-room"],
            },
        )
        self.assertEqual(res.status_code, 200)
        pid = res.json()["pin"]["id"]

        conor_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=usr-conor").json()["pins"]}
        alec_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=usr-alec").json()["pins"]}
        guest_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=guest").json()["pins"]}
        self.assertIn(pid, conor_pins)
        self.assertIn(pid, alec_pins)
        self.assertNotIn(pid, guest_pins)

    def test_alec_moves_charger_flow_and_ask_qa(self):
        self.client.post(
            "/api/zones",
            json={
                "world_id": "world_1",
                "name": "Bedside Table",
                "position": [0.5, 0.8, 0.2],
                "radius_m": 0.9,
                "created_by": "usr-conor",
            },
        )
        self.client.post(
            "/api/zones",
            json={
                "world_id": "world_1",
                "name": "Hotel Desk",
                "position": [-0.8, 0.8, -0.9],
                "radius_m": 0.9,
                "created_by": "usr-alec",
            },
        )

        r1 = self.client.post(
            "/api/objects/sighting",
            json={
                "world_id": "world_1",
                "label": "Phone Charger",
                "position": [0.5, 0.8, 0.2],
                "actor_user_id": "usr-conor",
                "zone_hint": "table",
            },
        ).json()
        self.assertEqual(r1["event_type"], "placed")

        r2 = self.client.post(
            "/api/objects/sighting",
            json={
                "world_id": "world_1",
                "label": "Phone Charger",
                "position": [-0.8, 0.8, -0.9],
                "actor_user_id": "usr-alec",
                "zone_hint": "desk",
            },
        ).json()
        self.assertEqual(r2["event_type"], "moved")

        ask = self.client.post(
            "/api/ask",
            json={"viewer_id": "usr-conor", "wearer_id": "wearer-b", "query": "Where is my charger?"},
        ).json()
        self.assertTrue(ask["found"])
        self.assertIn("Bedside Table", ask["spoken_answer"])
        self.assertIn("Hotel Desk", ask["spoken_answer"])
        self.assertIn("Alec", ask["spoken_answer"])

    def test_legacy_contracts_remain_valid(self):
        s = self.client.get("/api/state").json()
        for k in ("wearers", "objects", "pins", "snapshots", "diff", "point_count"):
            self.assertIn(k, s)
        cz = self.client.post("/api/ctrl-z").json()
        self.assertIn("diff", cz)


if __name__ == "__main__":
    unittest.main()
