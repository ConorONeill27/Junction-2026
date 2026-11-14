"""Persistent spatial notes with visual crop re-ID and 3D Studio (:8000) sync."""

import json
import math
from pathlib import Path
import threading
import time
import urllib.request
from uuid import uuid4

import cv2
import numpy as np

DEFAULT_NOTES_DIR = Path(__file__).resolve().parent / "notes"
STUDIO_BASE_URL = "http://127.0.0.1:8000"


def _compute_crop_signature(crop_bgr):
    """Compute a compact HSV color histogram + ORB descriptor signature for crop re-ID."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def _hist_similarity(hist_a, hist_b):
    if hist_a is None or hist_b is None:
        return 0.0
    return float(cv2.compareHist(hist_a.astype(np.float32), hist_b.astype(np.float32), cv2.HISTCMP_CORREL))


def _project_3d_to_2d(world_pt, cam_pos, cam_rot_3x3, width, height, fov_deg=70.0):
    """Project a 3D world point [x, y, z] into 2D image pixels (u, v) if in front of camera."""
    rel = np.asarray(world_pt, dtype=np.float64) - np.asarray(cam_pos, dtype=np.float64)
    cam_pt = np.asarray(cam_rot_3x3, dtype=np.float64).T @ rel
    # Camera frame: x right, y down, z forward
    if cam_pt[2] <= 0.15:
        return None
    focal = (max(width, height) * 0.5) / math.tan(math.radians(fov_deg * 0.5))
    u = width * 0.5 + focal * (cam_pt[0] / cam_pt[2])
    v = height * 0.5 + focal * (cam_pt[1] / cam_pt[2])
    if u < -80 or u > width + 80 or v < -80 or v > height + 80:
        return None
    return [float(u), float(v), float(cam_pt[2])]


class SpatialNoteStore:
    """Thread-safe persistent store for pinned notes across Person A -> Person B handoffs."""

    def __init__(self, directory=DEFAULT_NOTES_DIR, studio_url=STUDIO_BASE_URL):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / "notes.json"
        self.studio_url = studio_url.rstrip("/")
        self.lock = threading.Lock()
        self.notes = {}  # note_id -> dict
        self._signatures = {}  # note_id -> np.ndarray
        self._load()

    def _load(self):
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            for item in raw.get("notes", []):
                nid = item.get("note_id")
                if not nid:
                    continue
                self.notes[nid] = item
                crop_path = self.directory / f"{nid}.jpg"
                if crop_path.exists():
                    crop = cv2.imread(str(crop_path))
                    sig = _compute_crop_signature(crop)
                    if sig is not None:
                        self._signatures[nid] = sig
        except (OSError, ValueError):
            pass

    def _save(self):
        payload = {"notes": list(self.notes.values())}
        tmp = self.directory / "notes.tmp.json"
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.index_path)

    def list_notes(self):
        with self.lock:
            return [dict(n) for n in self.notes.values()]

    def clear(self):
        with self.lock:
            self.notes.clear()
            self._signatures.clear()
            for p in self.directory.glob("*.jpg"):
                p.unlink(missing_ok=True)
            self._save()

    def add_note(
        self,
        label,
        text,
        author="Person A",
        object_id=None,
        bbox_xyxy=None,
        frame_size_px=None,
        crop_bgr=None,
        position_3d=None,
        sync_studio=True,
        audience_type="friends",
        group_ids=None,
        target_user_ids=None,
        lat=None,
        lon=None,
    ):
        text = (text or "").strip()
        if not text:
            raise ValueError("Note text cannot be empty")
        label = (label or "object").strip() or "object"
        nid = uuid4().hex[:12]
        now = time.time()
        audience_type = audience_type or "friends"
        group_ids = list(group_ids or [])
        target_user_ids = list(target_user_ids or [])

        if crop_bgr is not None and crop_bgr.size > 0:
            cv2.imwrite(str(self.directory / f"{nid}.jpg"), crop_bgr)
            sig = _compute_crop_signature(crop_bgr)
        else:
            sig = None

        center_px = None
        if bbox_xyxy and len(bbox_xyxy) == 4:
            center_px = [
                round((float(bbox_xyxy[0]) + float(bbox_xyxy[2])) * 0.5, 1),
                round((float(bbox_xyxy[1]) + float(bbox_xyxy[3])) * 0.5, 1),
            ]

        if position_3d is None and sync_studio:
            position_3d = self._sync_to_studio(
                note_id=nid,
                label=label,
                text=text,
                author=author,
                center_px=center_px,
                frame_size_px=frame_size_px,
                audience_type=audience_type,
                group_ids=group_ids,
                target_user_ids=target_user_ids,
                lat=lat,
                lon=lon,
            )

        record = {
            "note_id": nid,
            "object_id": object_id or nid,
            "label": label,
            "text": text,
            "author": author,
            "audience_type": audience_type,
            "group_ids": group_ids,
            "target_user_ids": target_user_ids,
            "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "bbox_xyxy": [round(float(v), 1) for v in bbox_xyxy] if bbox_xyxy else None,
            "center_px": center_px,
            "frame_size_px": list(frame_size_px) if frame_size_px else None,
            "position_3d": [round(float(v), 3) for v in position_3d] if position_3d else None,
            "created_at": now,
        }
        with self.lock:
            self.notes[nid] = record
            if sig is not None:
                self._signatures[nid] = sig
            self._save()
        return dict(record)

    def _sync_to_studio(
        self,
        note_id,
        label,
        text,
        author,
        center_px,
        frame_size_px,
        audience_type="friends",
        group_ids=None,
        target_user_ids=None,
        lat=None,
        lon=None,
    ):
        if not self.studio_url:
            return None
        payload = json.dumps({
            "id": f"pin-{note_id}",
            "label": label,
            "text": text,
            "author": author,
            "center_px": center_px,
            "frame_size_px": frame_size_px,
            "audience_type": audience_type,
            "group_ids": list(group_ids or []),
            "target_user_ids": list(target_user_ids or []),
            "lat": lat,
            "lon": lon,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.studio_url}/api/pin-from-hud",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("position")
        except Exception:
            return None

    def annotate_detections(self, detections, frame, cam_pose=None):
        """Attach saved notes to matching live detections via ID, label+crop re-ID, or 3D projection."""
        with self.lock:
            if not self.notes:
                return detections
            notes_snapshot = list(self.notes.values())
            sigs_snapshot = dict(self._signatures)

        matched_note_ids = set()
        h, w = frame.shape[:2] if frame is not None else (0, 0)

        for det in detections:
            det_id = det.get("object_id")
            det_label = (det.get("label") or "").lower()
            best_note = None
            best_score = -1.0

            # 1. Exact object_id match (same session)
            for n in notes_snapshot:
                if det_id and n.get("object_id") == det_id:
                    best_note = n
                    best_score = 2.0
                    break

            # 2. Cross-wearer visual re-ID: same label + crop similarity
            if best_note is None and frame is not None:
                x1, y1, x2, y2 = det.get("bbox_xyxy", [0, 0, 0, 0])
                left, top = max(0, int(x1)), max(0, int(y1))
                right, bottom = min(w, int(x2)), min(h, int(y2))
                det_sig = _compute_crop_signature(frame[top:bottom, left:right]) if right > left and bottom > top else None
                for n in notes_snapshot:
                    if n["note_id"] in matched_note_ids:
                        continue
                    if (n.get("label") or "").lower() != det_label:
                        continue
                    saved_sig = sigs_snapshot.get(n["note_id"])
                    if saved_sig is not None and det_sig is not None:
                        sim = _hist_similarity(saved_sig, det_sig)
                    else:
                        sim = 0.5  # Label-only fallback when no crop was saved
                    if sim > best_score and sim >= 0.30:
                        best_score = sim
                        best_note = n

            if best_note is not None:
                matched_note_ids.add(best_note["note_id"])
                det["note"] = best_note["text"]
                det["note_id"] = best_note["note_id"]
                det["note_author"] = best_note.get("author", "Person A")
                if best_note.get("position_3d"):
                    det["position_3d"] = best_note["position_3d"]

        # 3. 3D-to-2D projection for any remaining notes when camera pose (pos, rot3x3) is available
        if cam_pose is not None and w > 0 and h > 0:
            cam_pos, cam_rot = cam_pose
            for n in notes_snapshot:
                if n["note_id"] in matched_note_ids or not n.get("position_3d"):
                    continue
                proj = _project_3d_to_2d(n["position_3d"], cam_pos, cam_rot, w, h)
                if proj is None:
                    continue
                u, v, depth_m = proj
                box_r = max(24.0, min(90.0, 120.0 / max(0.6, depth_m)))
                detections.append({
                    "object_id": n.get("object_id") or n["note_id"],
                    "label": n["label"],
                    "confidence": 0.99,
                    "bbox_xyxy": [u - box_r, v - box_r, u + box_r, v + box_r],
                    "center_px": [u, v],
                    "note": n["text"],
                    "note_id": n["note_id"],
                    "note_author": n.get("author", "Person A"),
                    "position_3d": n["position_3d"],
                    "anchor_source": "3d_projection",
                })
        return detections
