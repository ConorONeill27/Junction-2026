"""Exercise the phone-frame API without loading model weights or opening a camera."""
import json
import threading
import unittest
import urllib.error
import urllib.request

import cv2
import numpy as np

from detection_server import DetectionServer


class PhoneFrameTests(unittest.TestCase):
    def setUp(self):
        self.frames = []
        def predict(frame):
            self.frames.append(frame.shape)
            return [{"label": "test object", "bbox_xyxy": [10, 20, 30, 40], "center_px": [20, 30]}]
        self.server = DetectionServer("127.0.0.1", 0, predict).start()
        self.url = f"http://127.0.0.1:{self.server.http.server_port}"

    def tearDown(self):
        self.server.close()

    def post(self, body):
        return urllib.request.urlopen(urllib.request.Request(self.url + "/api/frame", body,
                                      {"Content-Type": "image/jpeg"}), timeout=5)

    def test_phone_orientation_and_snapshot(self):
        _, jpeg = cv2.imencode(".jpg", np.zeros((640, 480, 3), dtype=np.uint8))
        with self.post(jpeg.tobytes()) as response:
            result = json.load(response)
        self.assertEqual(result["source"], "phone_camera")
        self.assertEqual(result["frame_size_px"], [480, 640])
        self.assertEqual(result["detections"][0]["center_px"], [20, 30])
        self.assertEqual(self.frames, [(640, 480, 3)])
        with urllib.request.urlopen(self.url + "/api/detections") as response:
            self.assertEqual(json.load(response)["detections"], result["detections"])

    def test_phone_mode_bypasses_server_flow_and_keeps_legacy_mode(self):
        self.server.close()
        calls = []
        def predict(frame):
            calls.append('detect')
            return []
        def track(frame, rotation):
            calls.append('flow')
            return []
        self.server = DetectionServer('127.0.0.1', 0, predict=predict, track=track).start()
        self.url = f'http://127.0.0.1:{self.server.http.server_port}'
        _, jpeg = cv2.imencode('.jpg', np.zeros((100, 100, 3), dtype=np.uint8))
        request = urllib.request.Request(self.url + '/api/frame', jpeg.tobytes(),
            {'Content-Type': 'image/jpeg', 'X-Tracking-Mode': 'phone'})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(json.load(response)['tracking_mode'], 'phone')
        with self.post(jpeg.tobytes()) as response:
            self.assertEqual(json.load(response)['tracking_mode'], 'server')
        self.assertEqual(calls, ['detect', 'flow'])

    def test_invalid_frame_releases_inference_lock(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(b"not a jpeg")
        self.assertEqual(error.exception.code, 400)
        self.assertTrue(self.server.inference_lock.acquire(timeout=1))
        self.server.inference_lock.release()

    def test_landscape_frame_rotated_on_server(self):
        _, jpeg = cv2.imencode('.jpg', np.zeros((360, 640, 3), dtype=np.uint8))
        request = urllib.request.Request(self.url + '/api/frame', jpeg.tobytes(),
                                        {'Content-Type': 'image/jpeg', 'X-Image-Rotation': '90'})
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        self.assertEqual(result['frame_size_px'], [360, 640])
        self.assertEqual(self.frames, [(640, 360, 3)])

    def test_busy_and_size_limits(self):
        self.server.inference_lock.acquire()
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(b"frame")
            self.assertEqual(error.exception.code, 429)
        finally:
            self.server.inference_lock.release()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(b"")
        self.assertEqual(error.exception.code, 413)


if __name__ == "__main__":
    unittest.main()
