import tempfile
import unittest
from pathlib import Path

from db import SpatialDatabase
from social_store import seed_everyday_social_data


class AutoIndexerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SpatialDatabase(sqlite_path=Path(self.tmp.name) / "auto.db")
        seed_everyday_social_data(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_clusters_multi_view_3d_hits_and_assigns_zones(self):
        from auto_indexer import cluster_detections_3d, persist_indexed_world

        zone_hits = [
            {"raw_label": "bed", "conf": 0.85, "pos": [0.10, 0.45, -0.70], "kf_idx": 22},
            {"raw_label": "bed", "conf": 0.82, "pos": [0.18, 0.47, -0.66], "kf_idx": 24},
            {"raw_label": "desk", "conf": 0.88, "pos": [-0.62, 0.40, 1.15], "kf_idx": 76},
        ]
        obj_hits = [
            {"raw_label": "lamp", "conf": 0.91, "pos": [-0.65, 0.62, 1.18], "kf_idx": 78},
            {"raw_label": "lamp", "conf": 0.89, "pos": [-0.61, 0.64, 1.20], "kf_idx": 80},
            {"raw_label": "cup", "conf": 0.72, "pos": [1.35, 0.48, -1.10], "kf_idx": 8},
        ]
        zones, objs = cluster_detections_3d(zone_hits, obj_hits)
        self.assertGreaterEqual(len(zones), 2)
        self.assertGreaterEqual(len(objs), 2)

        lamp = next(o for o in objs if "lamp" in o["label"].lower())
        self.assertAlmostEqual(lamp["position"][0], -0.63, delta=0.05)

        summary = persist_indexed_world("world_1", self.db, zones, objs, clear_auto=True)
        self.assertEqual(summary["zones_count"], len(zones))
        self.assertEqual(summary["objects_count"], len(objs))
        db_zones = {z["name"] for z in self.db.list_zones("world_1")}
        self.assertIn("Desk", db_zones)

        # Re-persisting with clear_auto=True must cleanly replace prior auto rows without PK conflict
        summary2 = persist_indexed_world("world_1", self.db, zones, objs, clear_auto=True)
        self.assertEqual(summary2["zones_count"], len(zones))

    def test_filters_single_hit_noise_and_suppresses_synonym_overlaps(self):
        from auto_indexer import cluster_detections_3d

        zone_hits = [
            {"raw_label": "table", "conf": 0.72, "pos": [1.55, 0.44, -1.25], "kf_idx": 8},
            {"raw_label": "table", "conf": 0.78, "pos": [1.57, 0.43, -1.27], "kf_idx": 10},
            {"raw_label": "table", "conf": 0.69, "pos": [1.56, 0.45, -1.26], "kf_idx": 12},
            # Weak desk detection at exact same Tea Counter position -> suppressed
            {"raw_label": "desk", "conf": 0.41, "pos": [1.56, 0.44, -1.26], "kf_idx": 10},
            {"raw_label": "desk", "conf": 0.40, "pos": [1.56, 0.44, -1.26], "kf_idx": 11},
            {"raw_label": "desk", "conf": 0.42, "pos": [1.56, 0.44, -1.26], "kf_idx": 12},
            # Single-frame hallucination -> filtered out when min_zone_hits=3
            {"raw_label": "bed", "conf": 0.48, "pos": [1.33, 0.52, -1.20], "kf_idx": 36},
        ]
        obj_hits = [
            {"raw_label": "mug", "conf": 0.92, "pos": [1.45, 0.53, -1.23], "kf_idx": 8},
            {"raw_label": "mug", "conf": 0.89, "pos": [1.46, 0.53, -1.24], "kf_idx": 10},
            {"raw_label": "mug", "conf": 0.87, "pos": [1.45, 0.54, -1.22], "kf_idx": 12},
            # Single-frame hallucination -> filtered out
            {"raw_label": "kettle", "conf": 0.60, "pos": [1.02, 0.33, -3.04], "kf_idx": 6},
        ]
        zones, objs = cluster_detections_3d(
            zone_hits, obj_hits, min_zone_hits=3, min_object_hits=3, min_zone_conf=0.45, min_object_conf=0.50
        )
        self.assertEqual([z["label"] for z in zones], ["Tea Counter"])
        self.assertEqual([o["label"] for o in objs], ["Mug"])


if __name__ == "__main__":
    unittest.main()
