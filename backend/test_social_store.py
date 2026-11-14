import tempfile
import unittest
from pathlib import Path
from db import SpatialDatabase
from social_store import (
    can_user_view,
    evaluate_sighting_transition,
    resolve_zone,
    seed_everyday_social_data,
)


class SocialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SpatialDatabase(sqlite_path=Path(self.tmp.name) / "test.db")
        seed_everyday_social_data(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_visibility_scopes_and_guest_isolation(self):
        fam_pin = {
            "author_id": "usr-mum",
            "audience_type": "group",
            "group_ids": ["grp-family"],
            "target_user_ids": [],
        }
        priv_pin = {
            "author_id": "usr-conor",
            "audience_type": "private",
            "group_ids": [],
            "target_user_ids": [],
        }
        friend_pin = {
            "author_id": "usr-alex",
            "audience_type": "friends",
            "group_ids": [],
            "target_user_ids": [],
        }
        direct_pin = {
            "author_id": "usr-conor",
            "audience_type": "people",
            "group_ids": [],
            "target_user_ids": ["usr-sarah"],
        }
        pub_pin = {
            "author_id": "usr-alex",
            "audience_type": "public",
            "group_ids": [],
            "target_user_ids": [],
        }

        self.assertTrue(can_user_view(fam_pin, "usr-conor", self.db))
        self.assertFalse(can_user_view(fam_pin, "usr-alex", self.db))
        self.assertFalse(can_user_view(priv_pin, "usr-mum", self.db))
        self.assertTrue(can_user_view(friend_pin, "usr-conor", self.db))
        self.assertFalse(can_user_view(friend_pin, "guest", self.db))
        self.assertTrue(can_user_view(direct_pin, "usr-sarah", self.db))
        self.assertFalse(can_user_view(direct_pin, "usr-alex", self.db))
        self.assertTrue(can_user_view(pub_pin, "guest", self.db))

    def test_moved_vs_seen_prevents_false_moves(self):
        ev_type, ev = evaluate_sighting_transition(
            prev_obj={"position": [0.0, 0.8, 0.0], "seen_by": "Conor"},
            prev_zone="Kitchen Table",
            new_pos=[0.05, 0.8, 0.05],
            new_zone="Kitchen Table",
            actor_user_id="usr-mum",
            actor_name="Mum",
            label="Phone Charger",
            object_id="obj-charger",
            world_id="world_1",
            ts=100.0,
        )
        self.assertEqual(ev_type, "seen")
        self.assertIsNone(ev)

        ev_type2, ev2 = evaluate_sighting_transition(
            prev_obj={"position": [0.0, 0.8, 0.0], "seen_by": "Conor"},
            prev_zone="Kitchen Table",
            new_pos=[2.4, 0.9, 1.2],
            new_zone="Sitting Room Press",
            actor_user_id="usr-mum",
            actor_name="Mum",
            label="Phone Charger",
            object_id="obj-charger",
            world_id="world_1",
            ts=200.0,
        )
        self.assertEqual(ev_type2, "moved")
        self.assertIn("Kitchen Table", ev2["from_zone"])
        self.assertIn("Sitting Room Press", ev2["to_zone"])
        self.assertEqual(ev2["actor_name"], "Mum")

    def test_zone_resolution_and_legacy_purge_keeps_user_pins(self):
        self.db.upsert_pin(
            {
                "id": "pin-1",
                "author": "Alex",
                "text": "LIVE 400V PANEL",
                "pin_type": "warning",
                "position": [0, 1, 0],
                "landmark": "Breaker Panel",
                "created_at": 1.0,
                "age_label": "1m",
            }
        )
        self.db.upsert_pin(
            {
                "id": "pin-user-custom",
                "author": "Conor",
                "text": "Spare key here",
                "pin_type": "note",
                "position": [1, 1, 1],
                "landmark": "Hall",
                "created_at": 2.0,
                "age_label": "now",
            }
        )
        self.db.migrate_purge_legacy_industrial_seed()
        pin_ids = {p["id"] for p in self.db.list_pins()}
        self.assertNotIn("pin-1", pin_ids)
        self.assertIn("pin-user-custom", pin_ids)

        zones = [
            {
                "zone_id": "z1",
                "world_id": "world_1",
                "name": "Beside Left Bed",
                "x": -0.7,
                "y": 0.1,
                "z": 0.7,
                "radius_m": 1.0,
            }
        ]
        self.assertEqual(
            resolve_zone("world_1", [-0.72, 0.06, 0.70], zones, "floor"),
            "Beside Left Bed",
        )
        self.assertEqual(
            resolve_zone("world_1", [5.0, 0.0, 5.0], zones, "beside the window"),
            "beside the window",
        )


if __name__ == "__main__":
    unittest.main()
