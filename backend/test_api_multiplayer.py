import unittest
from fastapi.testclient import TestClient
import main as backend_main


class MultiplayerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(backend_main.app)

    def test_scoped_pin_filtering_by_viewer(self):
        res = self.client.post(
            "/api/pin",
            json={
                "author": "Dad",
                "author_id": "usr-dad",
                "text": "Spare house key is in hall drawer",
                "pin_type": "note",
                "position": [0.2, 0.9, -0.4],
                "landmark": "Hall Drawer",
                "audience_type": "group",
                "group_ids": ["grp-family"],
            },
        )
        self.assertEqual(res.status_code, 200)
        pid = res.json()["pin"]["id"]

        conor_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=usr-conor").json()["pins"]}
        alex_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=usr-alex").json()["pins"]}
        guest_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=guest").json()["pins"]}
        self.assertIn(pid, conor_pins)
        self.assertNotIn(pid, alex_pins)
        self.assertNotIn(pid, guest_pins)

    def test_mum_moves_charger_flow_and_ask_qa(self):
        self.client.post(
            "/api/zones",
            json={
                "world_id": "world_1",
                "name": "Kitchen Table",
                "position": [0.5, 0.8, 0.2],
                "radius_m": 0.9,
                "created_by": "usr-conor",
            },
        )
        self.client.post(
            "/api/zones",
            json={
                "world_id": "world_1",
                "name": "Press in Sitting Room",
                "position": [-0.8, 0.8, -0.9],
                "radius_m": 0.9,
                "created_by": "usr-mum",
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
                "actor_user_id": "usr-mum",
                "zone_hint": "cabinet",
            },
        ).json()
        self.assertEqual(r2["event_type"], "moved")

        ask = self.client.post(
            "/api/ask",
            json={"viewer_id": "usr-conor", "wearer_id": "wearer-b", "query": "Where is my charger?"},
        ).json()
        self.assertTrue(ask["found"])
        self.assertIn("Kitchen Table", ask["spoken_answer"])
        self.assertIn("Press in Sitting Room", ask["spoken_answer"])
        self.assertIn("Mum", ask["spoken_answer"])

    def test_legacy_contracts_remain_valid(self):
        s = self.client.get("/api/state").json()
        for k in ("wearers", "objects", "pins", "snapshots", "diff", "point_count"):
            self.assertIn(k, s)
        cz = self.client.post("/api/ctrl-z").json()
        self.assertIn("diff", cz)


if __name__ == "__main__":
    unittest.main()
