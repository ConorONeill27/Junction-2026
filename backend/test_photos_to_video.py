import tempfile
import unittest
from pathlib import Path

import cv2
from PIL import Image

from photos_to_video import pack_photos_to_mp4


class PhotosToVideoTests(unittest.TestCase):
    def test_packs_sorted_photos_into_readable_mp4(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos = root / "photos"
            photos.mkdir()
            for idx, color in enumerate([(200, 40, 40), (40, 200, 40), (40, 40, 200)]):
                Image.new("RGB", (640, 480), color=color).save(photos / f"img_{idx:02d}.jpg")

            out_mp4 = root / "scan.mp4"
            info = pack_photos_to_mp4(photos, out_mp4, fps=2.0, max_dim=640)
            self.assertEqual(info["frame_count"], 3)
            self.assertTrue(out_mp4.exists())

            cap = cv2.VideoCapture(str(out_mp4))
            try:
                count = 0
                while True:
                    ok, _ = cap.read()
                    if not ok:
                        break
                    count += 1
                self.assertEqual(count, 3)
            finally:
                cap.release()


if __name__ == "__main__":
    unittest.main()
