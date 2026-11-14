"""Passive Object Memory for Demo 3 (2-room bottle/item finder + privacy controls)."""

import base64
import json
from pathlib import Path
import threading
import time

import cv2

DEFAULT_MEMORY_DIR = Path(__file__).resolve().parent / "memory"


def _human_age(seconds: float) -> str:
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{int(seconds // 3600)}h ago"


class ObjectMemoryStore:
    """Stores last-seen frame, crop, and surrounding context for everyday objects."""

    def __init__(self, directory=DEFAULT_MEMORY_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / "memory.json"
        self.lock = threading.Lock()
        self.paused = False
        self.items = {}  # normalized label -> dict
        self._last_saved_at = {}  # label -> monotonic/wall timestamp to throttle disk writes
        self._load()

    def _load(self):
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            self.paused = bool(raw.get("paused", False))
            self.items = dict(raw.get("items", {}))
        except (OSError, ValueError):
            pass

    def _save(self):
        tmp = self.directory / "memory.tmp.json"
        tmp.write_text(
            json.dumps({"paused": self.paused, "items": self.items}, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.index_path)

    def set_paused(self, paused: bool) -> bool:
        with self.lock:
            self.paused = bool(paused)
            self._save()
            return self.paused

    def clear(self):
        with self.lock:
            self.items.clear()
            self._last_saved_at.clear()
            for p in self.directory.glob("*.jpg"):
                p.unlink(missing_ok=True)
            self._save()

    def observe(self, detections, frame, min_conf=0.45):
        """Passively update last-seen snapshot & nearby object context."""
        with self.lock:
            if self.paused or frame is None or frame.size == 0 or not detections:
                return
            now = time.time()
            h, w = frame.shape[:2]
            labels_all = [d.get("label", "") for d in detections if d.get("label")]
            updated = False
            for det in detections:
                if det.get("anchor_source"):
                    continue
                label = (det.get("label") or "").strip().lower()
                conf = float(det.get("confidence", 0.0))
                if not label or conf < min_conf:
                    continue
                if now - self._last_saved_at.get(label, 0.0) < 0.75:
                    continue
                bbox = det.get("bbox_xyxy") or [0, 0, 0, 0]
                x1, y1, x2, y2 = [int(v) for v in bbox]
                l, t = max(0, x1), max(0, y1)
                r, b = min(w, x2), min(h, y2)
                if r <= l or b <= t:
                    continue

                neighbors = []
                for other in labels_all:
                    ol = other.strip().lower()
                    if ol and ol != label and ol not in neighbors:
                        neighbors.append(ol)
                context = f"near {', '.join(neighbors[:3])}" if neighbors else "in room view"

                safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
                annotated = frame.copy()
                cv2.rectangle(annotated, (l, t), (r, b), (72, 235, 160), 3)
                cv2.putText(annotated, f"{label} ({context})", (l, max(24, t - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (72, 235, 160), 2, cv2.LINE_AA)
                frame_path = self.directory / f"{safe_name}_frame.jpg"
                crop_path = self.directory / f"{safe_name}_crop.jpg"
                cv2.imwrite(str(frame_path), annotated)
                cv2.imwrite(str(crop_path), frame[t:b, l:r])

                self.items[label] = {
                    "label": label,
                    "confidence": round(conf, 3),
                    "context": context,
                    "neighbors": neighbors[:5],
                    "bbox_xyxy": [l, t, r, b],
                    "frame_size_px": [w, h],
                    "last_seen": now,
                    "frame_file": frame_path.name,
                    "crop_file": crop_path.name,
                }
                self._last_saved_at[label] = now
                updated = True

            if updated:
                self._save()

    def list_memories(self):
        with self.lock:
            now = time.time()
            out = []
            for item in self.items.values():
                rec = dict(item)
                rec["age_label"] = _human_age(max(0.0, now - float(item.get("last_seen", now))))
                out.append(rec)
            out.sort(key=lambda x: x.get("last_seen", 0.0), reverse=True)
            return out

    def find(self, query: str):
        q = (query or "").strip().lower()
        if not q:
            return None
        with self.lock:
            now = time.time()
            match = self.items.get(q)
            if match is None:
                for k, v in self.items.items():
                    if q in k or k in q:
                        match = v
                        break
            if match is None:
                return None
            rec = dict(match)
            age = _human_age(max(0.0, now - float(rec.get("last_seen", now))))
            rec["age_label"] = age
            rec["summary"] = f"{rec['label'].title()} last seen {age}, {rec['context']}"
            frame_path = self.directory / rec["frame_file"]
            if frame_path.exists():
                rec["snapshot_b64"] = base64.b64encode(frame_path.read_bytes()).decode("ascii")
            return rec
