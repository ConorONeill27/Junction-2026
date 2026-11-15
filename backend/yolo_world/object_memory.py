"""Passive Object Memory for Demo 3 (2-room bottle/item finder + privacy controls)."""

import base64
import json
from pathlib import Path
import threading
import time

import cv2

DEFAULT_MEMORY_DIR = Path(__file__).resolve().parent / "memory"

PERSONAL_ITEM_LABELS = (
    "water bottle",
    "bottle",
    "thermos",
    "flask",
    "keys",
    "key",
    "wallet",
    "phone",
    "cell phone",
    "glasses",
    "sunglasses",
    "backpack",
    "bag",
    "handbag",
    "laptop",
    "headphones",
    "earbuds",
    "umbrella",
    "passport",
    "jacket",
    "charger",
    "cup",
)

FURNITURE_LABELS = (
    "bed",
    "nightstand",
    "desk",
    "table",
    "chair",
    "couch",
    "sofa",
    "bench",
    "shelf",
    "cabinet",
)

LABEL_ALIASES = {
    "water bottle": ("water bottle", "bottle", "thermos", "flask"),
    "bottle": ("bottle", "water bottle", "thermos", "flask"),
    "thermos": ("thermos", "water bottle", "bottle"),
    "bag": ("bag", "backpack", "handbag"),
    "backpack": ("backpack", "bag"),
    "phone": ("phone", "cell phone"),
    "cell phone": ("cell phone", "phone"),
}


def _human_age(seconds: float) -> str:
    if seconds < 10:
        return "just now"
    if seconds < 60:
        sec = int(seconds)
        return f"{sec} second{'s' if sec != 1 else ''} ago"
    if seconds < 3600:
        mins = int(seconds // 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hrs = int(seconds // 3600)
    return f"{hrs} hour{'s' if hrs != 1 else ''} ago"


def _spoken_label(label: str) -> str:
    norm = (label or "").strip().lower()
    if norm == "bottle":
        return "water bottle"
    if norm == "cell phone":
        return "phone"
    return norm or "item"


def _bbox_center(bbox):
    x1, y1, x2, y2 = bbox
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5


def _overlap_frac(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    area_a = max(1e-6, (ax2 - ax1) * (ay2 - ay1))
    return ((ix2 - ix1) * (iy2 - iy1)) / area_a


def _spatial_context(target_det, target_box, detections, width: int, height: int):
    """Derive nearby labels + human-friendly placement phrase from frame geometry."""
    label = (target_det.get("label") or "").strip().lower()
    l, t, r, b = target_box
    cx, cy = (l + r) * 0.5 / max(1, width), (t + b) * 0.5 / max(1, height)
    ny1, ny2 = t / max(1, height), b / max(1, height)
    nx1, nx2 = l / max(1, width), r / max(1, width)
    item_area = ((r - l) * (b - t)) / max(1, width * height)

    others = []
    beds = []
    people = []
    desk_or_chair_left = False
    for d in detections:
        if d.get("anchor_source"):
            continue
        ol = (d.get("label") or "").strip().lower()
        ob = d.get("bbox_xyxy") or [0, 0, 0, 0]
        if len(ob) != 4:
            continue
        ox1, oy1, ox2, oy2 = [float(v) for v in ob]
        if ol == "bed":
            beds.append((ox1 / width, oy1 / height, ox2 / width, oy2 / height))
        elif ol == "person":
            people.append((ox1, oy1, ox2, oy2))
        if ol in ("desk", "chair") and ((ox1 + ox2) * 0.5 / max(1, width)) < 0.6:
            desk_or_chair_left = True
        if not ol or ol == label:
            continue
        ocx, ocy = (ox1 + ox2) * 0.5 / max(1, width), (oy1 + oy2) * 0.5 / max(1, height)
        dist = ((cx - ocx) ** 2 + (cy - ocy) ** 2) ** 0.5
        others.append((dist, ol, (ox1 / width, oy1 / height, ox2 / width, oy2 / height)))

    others.sort(key=lambda item: item[0])
    neighbors = []
    for _, ol, _ in others:
        if ol not in neighbors:
            neighbors.append(ol)

    context = f"near {', '.join(neighbors[:3])}" if neighbors else "in room view"

    in_hand = item_area > 0.12 or any(_overlap_frac(target_box, pb) > 0.35 for pb in people)
    surface = ""
    for _, ol, (ox1, oy1, ox2, oy2) in others:
        if ol not in FURNITURE_LABELS:
            continue
        horz_overlap = min(nx2, ox2) - max(nx1, ox1)
        if horz_overlap > 0 and ny2 <= oy1 + 0.4 * max(0.05, oy2 - oy1) and ol in ("desk", "table", "nightstand", "chair", "couch", "sofa", "bench", "shelf"):
            surface = f"on the {ol}"
            break
    if not surface and cy >= 0.48 and ny2 >= 0.58 and not in_hand:
        surface = "on the ground"
    elif not surface and in_hand:
        surface = "in your hand"

    anchor_phrase = ""
    if beds:
        beds_sorted = sorted(beds, key=lambda bx: (bx[0] + bx[2]) * 0.5)
        nearest_idx, nearest_bed = min(
            enumerate(beds_sorted),
            key=lambda pair: abs(cx - (pair[1][0] + pair[1][2]) * 0.5),
        )
        bed_cx = (nearest_bed[0] + nearest_bed[2]) * 0.5
        if len(beds_sorted) >= 2:
            bed_name = "the left bed" if nearest_idx == 0 else "the right bed"
        elif bed_cx < 0.45 or (desk_or_chair_left and cx < bed_cx):
            bed_name = "the left bed"
        else:
            bed_name = "the bed"
        anchor_phrase = f"beside {bed_name}"
    elif others:
        anchor_label = next((ol for _, ol, _ in others if ol != "person"), others[0][1])
        anchor_phrase = f"near the {anchor_label}"

    if surface == "in your hand":
        spatial_phrase = f"{surface} {anchor_phrase}".strip()
    elif surface and anchor_phrase and not anchor_phrase.endswith(surface.replace("on the ", "")):
        spatial_phrase = f"{surface} {anchor_phrase}"
    elif surface:
        spatial_phrase = surface
    elif anchor_phrase:
        spatial_phrase = anchor_phrase
    else:
        spatial_phrase = context

    return context, spatial_phrase, in_hand


from studio_bridge import post_json_async


def _crop_hsv_hist(crop_bgr):
    """Compact 8x8 HS color histogram for distinguishing different instances of the same class."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return [round(float(v), 4) for v in hist.flatten()]


def _hist_corr(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    import numpy as np
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    return float(cv2.compareHist(va, vb, cv2.HISTCMP_CORREL))


class ObjectMemoryStore:
    """Stores last-seen frame, crop, and surrounding context for everyday objects."""

    def __init__(self, directory=DEFAULT_MEMORY_DIR, save_interval_s=3.0, studio_url=None, world_id="world_1", actor_user_id="usr_conor", api_token=""):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / "memory.json"
        self.save_interval_s = float(save_interval_s)
        self.studio_url = studio_url.rstrip("/") if studio_url else None
        self.world_id = world_id
        self.actor_user_id = actor_user_id
        self.api_token = (api_token or "").strip()
        self.lock = threading.Lock()
        self.paused = False
        self._epoch = 0
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

    def epoch(self) -> int:
        with self.lock:
            return self._epoch

    def set_paused(self, paused: bool) -> bool:
        with self.lock:
            self._epoch += 1
            self.paused = bool(paused)
            self._save()
            return self.paused

    def clear(self):
        with self.lock:
            self._epoch += 1
            self.items.clear()
            self._last_saved_at.clear()
            for p in self.directory.glob("*.jpg"):
                p.unlink(missing_ok=True)
            self._save()

    def observe(self, detections, frame, min_conf=0.40, expected_epoch=None):
        """Passively update last-seen snapshot & nearby object context."""
        with self.lock:
            if (
                self.paused
                or (expected_epoch is not None and self._epoch != expected_epoch)
                or frame is None
                or frame.size == 0
                or not detections
            ):
                return
            obs_epoch = self._epoch
            now = time.time()
            h, w = frame.shape[:2]
            updated = False
            used_keys_this_frame = set()
            for det in detections:
                if det.get("anchor_source"):
                    continue
                label = (det.get("label") or "").strip().lower()
                conf = float(det.get("confidence", 0.0))
                if not label or conf < min_conf:
                    continue
                bbox = det.get("bbox_xyxy") or [0, 0, 0, 0]
                x1, y1, x2, y2 = [int(v) for v in bbox]
                l, t = max(0, x1), max(0, y1)
                r, b = min(w, x2), min(h, y2)
                if r <= l or b <= t:
                    continue

                crop = frame[t:b, l:r]
                hist = _crop_hsv_hist(crop)

                # Match an existing instance of this class by visual appearance (HSV histogram),
                # or allocate "<label> #2" if two distinct instances are seen.
                existing_keys = [k for k, v in self.items.items() if v.get("label") == label and k not in used_keys_this_frame]
                target_key = None
                best_sim = -1.0
                for k in existing_keys:
                    sim = _hist_corr(hist, self.items[k].get("hist"))
                    if sim > best_sim:
                        best_sim = sim
                        target_key = k

                if target_key is None or (best_sim < 0.62 and len([k for k, v in self.items.items() if v.get("label") == label]) < 4):
                    if label not in self.items and label not in used_keys_this_frame:
                        target_key = label
                    else:
                        idx = 2
                        while f"{label} #{idx}" in self.items or f"{label} #{idx}" in used_keys_this_frame:
                            idx += 1
                        target_key = f"{label} #{idx}" if idx <= 4 else (target_key or label)

                used_keys_this_frame.add(target_key)
                if now - self._last_saved_at.get(target_key, 0.0) < 0.75:
                    continue

                context, spatial_phrase, in_hand = _spatial_context(det, (l, t, r, b), detections, w, h)
                neighbors = []
                for other in detections:
                    if other.get("anchor_source"):
                        continue
                    ol = (other.get("label") or "").strip().lower()
                    if ol and ol != label and ol not in neighbors:
                        neighbors.append(ol)

                safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in target_key)
                annotated = frame.copy()
                cv2.rectangle(annotated, (l, t), (r, b), (72, 235, 160), 3)
                tag = f"{_spoken_label(label)}: {spatial_phrase}"
                text_x = max(8, min(l, max(8, w - len(tag) * 11)))
                cv2.putText(annotated, tag, (text_x, max(24, t - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (72, 235, 160), 2, cv2.LINE_AA)
                frame_path = self.directory / f"{safe_name}_frame.jpg"
                crop_path = self.directory / f"{safe_name}_crop.jpg"
                cv2.imwrite(str(frame_path), annotated)
                cv2.imwrite(str(crop_path), crop)

                self.items[target_key] = {
                    "instance_id": target_key,
                    "label": label,
                    "confidence": round(conf, 3),
                    "context": context,
                    "spatial_phrase": spatial_phrase,
                    "in_hand": in_hand,
                    "neighbors": neighbors[:5],
                    "bbox_xyxy": [l, t, r, b],
                    "frame_size_px": [w, h],
                    "last_seen": now,
                    "frame_file": frame_path.name,
                    "crop_file": crop_path.name,
                    "hist": hist,
                }
                self._last_saved_at[target_key] = now
                updated = True

                if self.studio_url:
                    cx = ((l + r) * 0.5 / max(1, w) - 0.5) * 2.0
                    cy = (0.5 - (t + b) * 0.5 / max(1, h)) * 1.2 + 0.8
                    bridge_headers = {"X-WorldGit-Token": self.api_token} if self.api_token else None
                    post_json_async(
                        self.studio_url,
                        "/api/objects/sighting",
                        {
                            "world_id": self.world_id,
                            "label": label,
                            "position": [round(cx, 3), round(cy, 3), -1.2],
                            "actor_user_id": self.actor_user_id,
                            "zone_hint": spatial_phrase or context or "",
                            "confidence": round(conf, 3),
                            "unlocalized_2d": True,
                        },
                        headers=bridge_headers,
                        cancel_check=lambda: self.paused or self._epoch != obs_epoch,
                    )

            if updated:
                self._save()

    def list_memories(self):
        with self.lock:
            obs_epoch = self._epoch
            now = time.time()
            out = []
            for item in self.items.values():
                rec = dict(item)
                rec["age_label"] = _human_age(max(0.0, now - float(item.get("last_seen", now))))
                out.append(rec)
            out.sort(key=lambda x: x.get("last_seen", 0.0), reverse=True)
            return out

    def _lookup_all_locked(self, query: str):
        q = (query or "").strip().lower()
        if not q:
            return []
        candidates = LABEL_ALIASES.get(q, (q,))
        hits = []
        for cand in candidates:
            for k, v in self.items.items():
                lbl = (v.get("label") or k).lower()
                if lbl == cand or cand in k or k in cand or cand in lbl:
                    if v not in hits:
                        hits.append(v)
        hits.sort(key=lambda x: x.get("last_seen", 0.0), reverse=True)
        return hits

    def _lookup_locked(self, query: str):
        hits = self._lookup_all_locked(query)
        return hits[0] if hits else None

    def _with_snapshot(self, item: dict, now: float, instance_count: int = 1) -> dict:
        rec = dict(item)
        rec.pop("hist", None)
        age = _human_age(max(0.0, now - float(rec.get("last_seen", now))))
        rec["age_label"] = age
        rec["instance_count"] = instance_count
        loc = rec.get("spatial_phrase") or rec.get("context") or "in room view"
        ctx = rec.get("context") or ""
        multi_prefix = f"{instance_count} {rec['label']}s tracked; latest " if instance_count > 1 else ""
        if ctx and ctx not in loc:
            rec["summary"] = f"{multi_prefix}{rec['label'].title()} last seen {age}, {loc} ({ctx})"
        else:
            rec["summary"] = f"{multi_prefix}{rec['label'].title()} last seen {age}, {loc}"
        frame_path = self.directory / rec["frame_file"]
        if frame_path.exists():
            rec["snapshot_b64"] = base64.b64encode(frame_path.read_bytes()).decode("ascii")
        return rec

    def find(self, query: str):
        q = (query or "").strip().lower()
        if not q:
            return None
        with self.lock:
            hits = self._lookup_all_locked(q)
            if not hits:
                return None
            obs_epoch = self._epoch
            now = time.time()
            rec = self._with_snapshot(hits[0], now, instance_count=len(hits))
            rec["ambiguous"] = len(hits) > 1
            rec["ambiguity"] = (
                f"{len(hits)} {hits[0]['label']} observations in memory; showing latest."
                if len(hits) > 1 else ""
            )
            if len(hits) > 1:
                rec["instances"] = [
                    {
                        "instance_id": h.get("instance_id", h.get("label")),
                        "age_label": _human_age(max(0.0, now - float(h.get("last_seen", now)))),
                        "spatial_phrase": h.get("spatial_phrase") or h.get("context") or "in room view",
                    }
                    for h in hits
                ]
            return rec

    def ask(self, question: str):
        """Answer natural-language memory queries using only recorded observations."""
        raw = (question or "").strip()
        q = raw.lower()
        for wake in ("hey meta", "ok meta", "meta"):
            q = q.replace(wake, " ")
        q = " ".join(q.split())

        with self.lock:
            obs_epoch = self._epoch
            now = time.time()
            match = None
            matched_hits = []
            matched_explicitly = False
            candidate_labels = list(dict.fromkeys([
                *PERSONAL_ITEM_LABELS,
                *(str(v.get("label") or "").lower() for v in self.items.values() if v.get("label")),
                *self.items.keys(),
            ]))
            candidate_labels.sort(key=len, reverse=True)
            for cand in candidate_labels:
                if cand and cand in q:
                    hits = self._lookup_all_locked(cand)
                    if hits:
                        match = hits[0]
                        matched_hits = hits
                        matched_explicitly = True
                        break

            left_behind_intent = any(w in q for w in ("left", "behind", "forget", "forgot", "anything", "miss"))
            if match is None and (left_behind_intent or not q):
                personal = [
                    v for k, v in self.items.items()
                    if any(p in k or k in p for p in PERSONAL_ITEM_LABELS)
                ]
                if not personal:
                    personal = [
                        v for k, v in self.items.items()
                        if k not in FURNITURE_LABELS and k != "person"
                    ]
                if personal:
                    personal.sort(
                        key=lambda x: (
                            0 if not x.get("in_hand") else 1,
                            -float(x.get("last_seen", 0.0)),
                        )
                    )
                    match = personal[0]
                    matched_hits = self._lookup_all_locked(match.get("label", "")) or [match]

            if match is None:
                return {
                    "found": False,
                    "query": raw,
                    "spoken_response": "I don't have a recent observation matching that.",
                    "summary": "I don't have a recent observation matching that.",
                }

            instance_count = max(1, len(matched_hits))
            rec = self._with_snapshot(match, now, instance_count=instance_count)
            spoken_name = _spoken_label(rec["label"])
            age = rec["age_label"]
            loc = rec.get("spatial_phrase") or rec.get("context") or "in the room"
            if matched_explicitly:
                spoken = f"Last seen {age} {loc}."
            else:
                spoken = f"{spoken_name.capitalize()}, last seen {age} {loc}."
            ambiguity = ""
            if instance_count > 1:
                ambiguity = f"{instance_count} {rec['label']} observations in memory; showing latest."
                spoken = f"{spoken} ({ambiguity})"
            return {
                "found": True,
                "query": raw,
                "spoken_response": spoken,
                "summary": spoken,
                "item": rec,
                "age_label": age,
                "context": loc,
                "instance_count": instance_count,
                "ambiguous": instance_count > 1,
                "ambiguity": ambiguity,
                "snapshot_b64": rec.get("snapshot_b64", ""),
            }
