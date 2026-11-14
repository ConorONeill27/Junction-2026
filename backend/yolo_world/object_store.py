"""Associate overlapping boxes and save a current crop for each object ID."""

import json
import math
from pathlib import Path
import time
from uuid import uuid4

DEFAULT_OBJECTS_DIR = Path(__file__).resolve().parent / "objects"


def overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = max(0, a[2] - a[0]) * max(0, a[3] - a[1]) + max(0, b[2] - b[0]) * max(0, b[3] - b[1]) - intersection
    return intersection / union if union else 0


class ObjectStore:
    def __init__(self, directory=DEFAULT_OBJECTS_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.tracks = {}

    def update(self, detections, frame):
        import cv2

        now = time.time()
        self.tracks = {key: value for key, value in self.tracks.items() if now - value["last_seen"] < 10}
        # Match globally by strongest same-label overlap, one detection per ID.
        candidates = sorted(
            ((overlap(d["bbox_xyxy"], track["bbox_xyxy"]), index, key)
             for index, d in enumerate(detections) for key, track in self.tracks.items()
             if d["label"] == track["label"]), reverse=True
        )
        assignments = {}
        used = set()
        for score, index, key in candidates:
            if score >= 0.3 and index not in assignments and key not in used:
                assignments[index] = key
                used.add(key)
        height, width = frame.shape[:2]
        current_ids = set()
        for index, detection in enumerate(detections):
            key = assignments.get(index) or uuid4().hex
            detection["object_id"] = key
            x1, y1, x2, y2 = detection["bbox_xyxy"]
            left, top = max(0, min(width, math.floor(x1))), max(0, min(height, math.floor(y1)))
            right, bottom = max(0, min(width, math.ceil(x2))), max(0, min(height, math.ceil(y2)))
            if right <= left or bottom <= top:
                continue
            folder = self.directory / key
            folder.mkdir(exist_ok=True)
            image_path = folder / "image.jpg"
            temporary_image = folder / "image.tmp.jpg"
            if not cv2.imwrite(str(temporary_image), frame[top:bottom, left:right]):
                raise OSError(f"Could not save crop: {image_path}")
            temporary_image.replace(image_path)
            previous = self.tracks.get(key, {})
            metadata = {**detection, "center_xy_px": [(x1 + x2) / 2, (y1 + y2) / 2],
                        "frame_size_px": [width, height], "first_seen": previous.get("first_seen", now),
                        "last_seen": now}
            temporary_metadata = folder / "metadata.tmp"
            temporary_metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            temporary_metadata.replace(folder / "metadata.json")
            self.tracks[key] = metadata
            current_ids.add(key)
        self._remove_stale_objects(current_ids)

    def _remove_stale_objects(self, current_ids):
        """Remove only generated files inside stale UUID folders in this store."""
        root = self.directory.resolve()
        for folder in self.directory.iterdir():
            if (folder.name in current_ids or len(folder.name) != 32
                    or any(char not in "0123456789abcdef" for char in folder.name)
                    or folder.is_symlink() or not folder.is_dir()
                    or folder.resolve().parent != root):
                continue
            for name in ("image.jpg", "metadata.json", "image.tmp.jpg", "metadata.tmp"):
                (folder / name).unlink(missing_ok=True)
            if not any(folder.iterdir()):
                folder.rmdir()
