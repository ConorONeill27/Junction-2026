import time
import unittest
import cv2
import numpy as np
from flow_tracker import BoxTracker, TrackingPipeline


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.random.default_rng(12).integers(0, 255, (240, 320, 3), dtype=np.uint8)
        self.items = [{'label': 'test', 'object_id': 'stable', 'bbox_xyxy': [60, 60, 160, 160]}]

    def test_translation_preserves_identity(self):
        tracker = BoxTracker()
        tracker.reset(self.frame, np.eye(3), self.items)
        moved = cv2.warpAffine(self.frame, np.float32([[1, 0, 7], [0, 1, 4]]), (320, 240))
        items = tracker.update(moved, np.eye(3))
        self.assertEqual(items[0]['object_id'], 'stable')
        np.testing.assert_allclose(items[0]['bbox_xyxy'], [67, 64, 167, 164], atol=1)

    def test_sensor_prior_and_rotation(self):
        tracker = BoxTracker()
        tracker.reset(self.frame, np.eye(3), self.items)
        a = .015
        rotation = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        k = np.array([[288, 0, 160], [0, 288, 120], [0, 0, 1.]])
        moved = cv2.warpPerspective(self.frame, k @ rotation.T @ np.linalg.inv(k), (320, 240))
        items = tracker.update(moved, rotation)
        self.assertEqual(len(items), 1)
        self.assertLess(items[0]['center_px'][0], 110)

    def test_texture_loss_hides_label(self):
        tracker = BoxTracker()
        tracker.reset(np.zeros_like(self.frame), None, self.items)
        self.assertEqual(tracker.update(np.zeros_like(self.frame), None), [])

    def test_downsampled_tracking_returns_original_pixel_coordinates(self):
        frame = cv2.resize(self.frame, (640, 480))
        tracker = BoxTracker()
        tracker.reset(frame, None, [{'label': 'large', 'bbox_xyxy': [120, 120, 320, 320]}])
        moved = cv2.warpAffine(frame, np.float32([[1, 0, 10], [0, 1, 6]]), (640, 480))
        items = tracker.update(moved, None)
        np.testing.assert_allclose(items[0]['bbox_xyxy'], [130, 126, 330, 326], atol=1.5)

    def test_slow_detection_does_not_block_tracking_and_replays(self):
        import threading
        release = threading.Event()
        calls = []
        def detect(frame):
            calls.append(1)
            release.wait(2)
            return self.items
        pipeline = TrackingPipeline(detect, interval=1.5)
        try:
            pipeline(self.frame)
            moved = cv2.warpAffine(self.frame, np.float32([[1, 0, 7], [0, 1, 4]]), (320, 240))
            pipeline(moved)
            self.assertEqual(len(calls), 1)
            release.set()
            pipeline.pending.result(timeout=2)
            items = pipeline(moved)
            np.testing.assert_allclose(items[0]['bbox_xyxy'], [67, 64, 167, 164], atol=1)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            pipeline.close()

    def test_five_fps_cadence_does_not_drift_with_late_camera_frames(self):
        from unittest.mock import patch
        calls = []
        def detect(frame):
            calls.append(1)
            return []
        pipeline = TrackingPipeline(detect, interval=0.2)
        try:
            with patch('flow_tracker.time.monotonic') as clock:
                for index in range(20):
                    clock.return_value = index * 0.055
                    pipeline(self.frame)
                    if pipeline.pending is not None:
                        pipeline.pending.result(timeout=2)
            # Detection slots at 0, .2, .4, .6, .8, 1 second, despite frame jitter.
            self.assertEqual(len(calls), 6)
        finally:
            pipeline.close()


if __name__ == '__main__':
    unittest.main()
