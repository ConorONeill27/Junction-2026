"""Visual Place Anchors (ORB + RANSAC homography) for Demo 2 restaurant reviews."""

import base64
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

import cv2
import numpy as np

DEFAULT_PLACES_DIR = Path(__file__).resolve().parent / "places"


class PlaceAnchorStore:
    """Matches live glasses frames against saved storefront photos using ORB + homography."""

    def __init__(self, directory=DEFAULT_PLACES_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / "places.json"
        self.lock = threading.Lock()
        self.orb = cv2.ORB_create(nfeatures=600)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.places = {}  # place_id -> dict
        self._features = {}  # place_id -> (kp, des, w, h)
        self._load()

    def _extract_features(self, bgr_img):
        if bgr_img is None or bgr_img.size == 0:
            return None
        h, w = bgr_img.shape[:2]
        scale = min(1.0, 480.0 / max(h, w))
        small = cv2.resize(bgr_img, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1.0 else bgr_img
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        kp, des = self.orb.detectAndCompute(gray, None)
        if des is None or len(kp) < 8:
            return None
        return (kp, des, small.shape[1], small.shape[0])

    def _load(self):
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            for item in raw.get("places", []):
                pid = item.get("place_id")
                if not pid:
                    continue
                self.places[pid] = item
                img_path = self.directory / f"{pid}.jpg"
                if img_path.exists():
                    feat = self._extract_features(cv2.imread(str(img_path)))
                    if feat is not None:
                        self._features[pid] = feat
        except (OSError, ValueError):
            pass

    def _save(self):
        tmp = self.directory / "places.tmp.json"
        tmp.write_text(json.dumps({"places": list(self.places.values())}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.index_path)

    def list_places(self):
        with self.lock:
            return [dict(p) for p in self.places.values()]

    def clear(self):
        with self.lock:
            self.places.clear()
            self._features.clear()
            for p in self.directory.glob("*.jpg"):
                p.unlink(missing_ok=True)
            self._save()

    def add_place(self, name, rating, review, author="Friend", image_bgr=None, image_b64=None):
        name = (name or "").strip()
        review = (review or "").strip()
        if not name or not review:
            raise ValueError("Place name and review are required")
        rating = max(1, min(5, int(rating)))
        if image_bgr is None and image_b64:
            raw = base64.b64decode(image_b64.split(",")[-1])
            image_bgr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("Reference storefront image is required")

        pid = uuid4().hex[:12]
        feat = self._extract_features(image_bgr)
        cv2.imwrite(str(self.directory / f"{pid}.jpg"), image_bgr)
        record = {
            "place_id": pid,
            "name": name,
            "rating": rating,
            "review": review,
            "author": author,
            "created_at": time.time(),
        }
        with self.lock:
            self.places[pid] = record
            if feat is not None:
                self._features[pid] = feat
            self._save()
        return dict(record)

    def match_frame(self, frame_bgr, min_inliers=12):
        """Match live camera frame against saved Place Anchors; returns HUD detection items."""
        with self.lock:
            if not self._features or frame_bgr is None or frame_bgr.size == 0:
                return []
            items = [(pid, dict(self.places[pid]), self._features[pid]) for pid in self._features if pid in self.places]

        fh, fw = frame_bgr.shape[:2]
        scale = min(1.0, 480.0 / max(fh, fw))
        small = cv2.resize(frame_bgr, (max(1, int(fw * scale)), max(1, int(fh * scale)))) if scale < 1.0 else frame_bgr
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        kp_frame, des_frame = self.orb.detectAndCompute(gray, None)
        if des_frame is None or len(kp_frame) < min_inliers:
            return []

        results = []
        inv_scale = 1.0 / scale
        for pid, meta, (kp_ref, des_ref, rw, rh) in items:
            matches = self.matcher.knnMatch(des_ref, des_frame, k=2)
            good = [m for pair in matches if len(pair) == 2 for m, n in [pair] if m.distance < 0.75 * n.distance]
            if len(good) < min_inliers:
                continue
            src_pts = np.float32([kp_ref[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp_frame[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            if H is None or mask is None:
                continue
            inliers = int(mask.ravel().sum())
            if inliers < min_inliers:
                continue
            corners = np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]]).reshape(-1, 1, 2)
            proj = cv2.perspectiveTransform(corners, H).reshape(-1, 2) * inv_scale
            x1, y1 = float(np.min(proj[:, 0])), float(np.min(proj[:, 1]))
            x2, y2 = float(np.max(proj[:, 0])), float(np.max(proj[:, 1]))
            if x2 - x1 < 25 or y2 - y1 < 25 or x2 < 0 or y2 < 0 or x1 > fw or y1 > fh:
                continue
            results.append({
                "object_id": f"place-{pid}",
                "label": f"{meta['name']} ({meta['rating']}/5)",
                "confidence": round(min(0.99, inliers / 30.0), 3),
                "bbox_xyxy": [max(0.0, x1), max(0.0, y1), min(float(fw), x2), min(float(fh), y2)],
                "center_px": [max(0.0, min(float(fw), (x1 + x2) * 0.5)), max(0.0, min(float(fh), (y1 + y2) * 0.5))],
                "note": f"{meta['author']}: {meta['review']}",
                "anchor_source": "place_anchor",
            })
        return results
