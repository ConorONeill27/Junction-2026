"""Automatic 3D zone discovery and object tracking from reconstructed keyframes & 3D point clouds."""

import base64
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from social_store import resolve_zone
from video_localizer import DEFAULT_ROOM_PHOTOS_DIR, _get_yolo, _render_splat

ZONE_PROMPTS = (
    "bed",
    "nightstand",
    "desk",
    "table",
    "counter",
    "sideboard",
    "chair",
    "armchair",
    "tv",
    "door",
    "wardrobe",
    "sofa",
)

OBJECT_PROMPTS = (
    "water bottle",
    "bottle",
    "cup",
    "mug",
    "kettle",
    "phone",
    "laptop",
    "backpack",
    "bag",
    "keys",
    "wallet",
    "charger",
    "headphones",
    "lamp",
    "pillow",
    "notebook",
    "remote",
)

_ZONE_NAME_MAP = {
    "bed": "Bed",
    "nightstand": "Nightstand",
    "desk": "Desk",
    "table": "Tea Counter",
    "counter": "Tea Counter",
    "sideboard": "Tea Counter",
    "chair": "Desk Chair",
    "armchair": "Lounge Chair",
    "sofa": "Lounge Chair",
    "tv": "TV Wall",
    "door": "Entrance Door",
    "wardrobe": "Wardrobe",
}

_OBJECT_NAME_MAP = {
    "bottle": "Bottle",
    "water bottle": "Water Bottle",
    "cup": "Mug",
    "mug": "Mug",
    "kettle": "Kettle",
    "phone": "Phone",
    "laptop": "Laptop",
    "backpack": "Backpack",
    "bag": "Bag",
    "keys": "Keys",
    "wallet": "Wallet",
    "charger": "Charger",
    "headphones": "Headphones",
    "lamp": "Lamp",
    "pillow": "Pillow",
    "notebook": "Notepad",
    "remote": "Remote",
}


def _euclidean(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a[:3], b[:3])))


def _xz_dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt((float(a[0]) - float(b[0])) ** 2 + (float(a[2]) - float(b[2])) ** 2)


def _cluster_group(
    hits: List[Dict[str, Any]],
    radius_m: float,
    canonical_map: Dict[str, str],
    min_hits: int = 1,
    min_best_conf: float = 0.0,
) -> List[Dict[str, Any]]:
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for h in hits:
        raw = str(h.get("raw_label") or "").strip().lower()
        cname = canonical_map.get(raw, raw.title())
        if not cname or not isinstance(h.get("pos"), (list, tuple)) or len(h["pos"]) < 3:
            continue
        by_name.setdefault(cname, []).append(h)

    clusters: List[Dict[str, Any]] = []
    for cname, items in by_name.items():
        # Greedy spatial clustering weighted by confidence
        items_sorted = sorted(items, key=lambda x: float(x.get("conf", 0.5)), reverse=True)
        local_clusters: List[List[Dict[str, Any]]] = []
        for item in items_sorted:
            placed = False
            for cl in local_clusters:
                centroid = [
                    sum(float(p["pos"][i]) for p in cl) / len(cl)
                    for i in range(3)
                ]
                if _euclidean(item["pos"], centroid) <= radius_m:
                    cl.append(item)
                    placed = True
                    break
            if not placed:
                local_clusters.append([item])

        for cl in local_clusters:
            # Count distinct viewpoints (keyframes) to reject single-frame flicker
            distinct_kfs = len({p.get("kf_idx") for p in cl if p.get("kf_idx") is not None}) or len(cl)
            best_hit = max(cl, key=lambda p: float(p.get("conf", 0.5)))
            best_conf = float(best_hit.get("conf", 0.6))
            if distinct_kfs < min_hits:
                continue
            if best_conf < min_best_conf:
                continue
            weights = [max(0.1, float(p.get("conf", 0.5))) ** 2 for p in cl]
            w_sum = sum(weights)
            pos = [
                round(sum(float(p["pos"][i]) * w for p, w in zip(cl, weights)) / w_sum, 3)
                for i in range(3)
            ]
            clusters.append({
                "base_label": cname,
                "label": cname,
                "position": pos,
                "confidence": round(best_conf, 3),
                "hits": distinct_kfs,
                "best_kf_idx": best_hit.get("kf_idx"),
                "best_crop_b64": best_hit.get("crop_b64"),
                "raw_label": best_hit.get("raw_label", cname.lower()),
            })
    return clusters


def _suppress_overlapping(items: List[Dict[str, Any]], min_sep_m: float, xz_only: bool = True, anchor_boost: Optional[set] = None) -> List[Dict[str, Any]]:
    """Keep strongest multi-view cluster when distinct prompts hit the exact same spot."""
    dist_fn = _xz_dist if xz_only else _euclidean
    anchors = anchor_boost or set()
    ranked = sorted(
        items,
        key=lambda c: (
            c["hits"] * (0.5 + c["confidence"]) * (1.45 if c.get("base_label") in anchors else 1.0),
            c["confidence"],
        ),
        reverse=True,
    )
    kept: List[Dict[str, Any]] = []
    for c in ranked:
        if any(dist_fn(c["position"], ex["position"]) < min_sep_m for ex in kept):
            continue
        kept.append(c)
    return kept


def cluster_detections_3d(
    zone_hits: List[Dict[str, Any]],
    object_hits: List[Dict[str, Any]],
    zone_radius_m: float = 0.95,
    object_radius_m: float = 0.68,
    min_zone_hits: int = 1,
    min_object_hits: int = 1,
    min_zone_conf: float = 0.0,
    min_object_conf: float = 0.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Cluster multi-view 3D raycast hits into clean named Zones and Tracked Objects."""
    zones_raw = _cluster_group(
        zone_hits,
        radius_m=zone_radius_m,
        canonical_map=_ZONE_NAME_MAP,
        min_hits=min_zone_hits,
        min_best_conf=min_zone_conf,
    )
    zones = _suppress_overlapping(
        zones_raw,
        min_sep_m=0.58,
        xz_only=True,
        anchor_boost={"Bed", "Desk", "Tea Counter", "Nightstand", "Entrance Door"},
    )

    # Disambiguate multiple distinct zones sharing a base label (e.g. Inner Bed vs Window Bed)
    zone_counts: Dict[str, int] = {}
    for z in zones:
        zone_counts[z["base_label"]] = zone_counts.get(z["base_label"], 0) + 1
    seen_z: Dict[str, int] = {}
    bed_zones = sorted([z for z in zones if z["base_label"] == "Bed"], key=lambda z: z["position"][2])
    if len(bed_zones) == 2:
        bed_zones[0]["label"] = "Inner Bed"
        bed_zones[1]["label"] = "Window Bed"
    for z in zones:
        bl = z["base_label"]
        if bl == "Bed" and len(bed_zones) == 2:
            continue
        if zone_counts[bl] > 1:
            seen_z[bl] = seen_z.get(bl, 0) + 1
            z["label"] = f"{bl} {seen_z[bl]}"

    objs_raw = _cluster_group(
        object_hits,
        radius_m=object_radius_m,
        canonical_map=_OBJECT_NAME_MAP,
        min_hits=min_object_hits,
        min_best_conf=min_object_conf,
    )
    dedup_objs = _suppress_overlapping(objs_raw, min_sep_m=0.14, xz_only=False)

    # Name multiple instances of the same portable item cleanly using nearest zone if distinct
    obj_counts: Dict[str, int] = {}
    for o in dedup_objs:
        obj_counts[o["base_label"]] = obj_counts.get(o["base_label"], 0) + 1
    used_names: set = set()
    seen_o: Dict[str, int] = {}
    for o in dedup_objs:
        bl = o["base_label"]
        if obj_counts[bl] > 1:
            if zones:
                nearest_z = min(zones, key=lambda z: _euclidean(o["position"], z["position"]))
                if _euclidean(o["position"], nearest_z["position"]) <= 1.35:
                    prefix = "Tea Counter" if nearest_z["base_label"] == "Tea Counter" else nearest_z["label"].split()[0]
                    cand = f"{prefix} {bl}"
                    if cand not in used_names:
                        o["label"] = cand
                        used_names.add(cand)
                        continue
            seen_o[bl] = seen_o.get(bl, 0) + 1
            o["label"] = f"{bl} {seen_o[bl]}"
            used_names.add(o["label"])
    return zones, dedup_objs


def _refine_names_with_gemini(
    zones: List[Dict[str, Any]],
    objs: List[Dict[str, Any]],
) -> None:
    """Optionally refine zone & object names using Gemini 2.5 Flash on cropped reference views."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return
    candidates = []
    for i, z in enumerate(zones):
        if z.get("best_crop_b64"):
            candidates.append(("zone", i, z["label"], z["best_crop_b64"]))
    for i, o in enumerate(objs):
        if o.get("best_crop_b64"):
            candidates.append(("obj", i, o["label"], o["best_crop_b64"]))
    if not candidates:
        return

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        parts: List[Any] = [
            "You are labeling real furniture ZONES and portable everyday OBJECTS in a scanned hotel room. "
            "For each numbered crop below, return ONLY a JSON array of objects with keys: "
            "id (int matching the number), name (short 1-3 word everyday name, e.g. 'Tea Counter', 'Entrance Door', 'Study Desk', 'Gold Lamp', 'Coffee Mugs', 'Shaving Spray'). "
            "Keep names concise and accurate to what is visible."
        ]
        for idx, (kind, _arr_idx, fallback_name, b64) in enumerate(candidates[:14]):
            parts.append(f"#{idx} ({kind}, rough='{fallback_name}'):")
            parts.append(types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/jpeg"))

        resp = client.models.generate_content(model="gemini-2.5-flash", contents=parts)
        raw = resp.text or ""
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return
        items = json.loads(m.group(0))
        if not isinstance(items, list):
            return
        for row in items:
            cid = row.get("id")
            new_name = str(row.get("name") or "").strip()
            if isinstance(cid, int) and 0 <= cid < len(candidates[:14]) and new_name:
                kind, arr_idx, _, _ = candidates[cid]
                if kind == "zone":
                    zones[arr_idx]["label"] = new_name[:32]
                else:
                    objs[arr_idx]["label"] = new_name[:32]
    except Exception:
        pass


def match_photo_orientation(
    bgr: np.ndarray,
    cam_pos: np.ndarray,
    xyz: np.ndarray,
    rgb: np.ndarray,
    fw: int = 180,
) -> Tuple[float, float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Match a keyframe photo orientation (yaw, pitch) against the colored 3D splat point cloud."""
    oh, ow = bgr.shape[:2]
    fh = max(120, min(320, int(round(fw * (oh / max(1, ow))))))
    focal = round(fw * 1.16, 1)

    q_bgr = cv2.GaussianBlur(cv2.resize(bgr, (fw, fh)), (9, 9), 0)
    q_lab = cv2.cvtColor(q_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    def _score(yaw_deg: float, pitch_deg: float) -> float:
        img, _, _, _, _ = _render_splat(xyz, rgb, cam_pos, yaw_deg, pitch_deg, fw=fw, fh=fh, focal=focal)
        m = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 0).astype(np.float32)
        bm = cv2.GaussianBlur(m, (9, 9), 0)
        vm = bm > 0.15
        cov = float(vm.sum()) / float(fw * fh)
        if cov < 0.25:
            return 999.0
        bimg = cv2.GaussianBlur(img.astype(np.float32) * m[:, :, None], (9, 9), 0) / np.maximum(1e-3, bm[:, :, None])
        r_lab = cv2.cvtColor(np.clip(bimg, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
        diff = np.abs(r_lab[vm] - q_lab[vm])
        return float((diff[:, 0] + 1.5 * diff[:, 1] + 1.5 * diff[:, 2]).mean()) - cov * 10.0

    best = (999.0, 0.0, -8.0)
    for pitch in (-20.0, -8.0, 2.0):
        for yaw in range(0, 360, 15):
            s = _score(float(yaw), pitch)
            if s < best[0]:
                best = (s, float(yaw), pitch)

    _, cy, cp = best
    for pitch in (cp - 5.0, cp, cp + 5.0):
        for dy in (-10.0, -5.0, 0.0, 5.0, 10.0):
            s = _score(cy + dy, pitch)
            if s < best[0]:
                best = (s, cy + dy, pitch)

    _, fyaw, fpitch = best
    _, depth, fwd, right, cam_up = _render_splat(xyz, rgb, cam_pos, fyaw, fpitch, fw=fw, fh=fh, focal=focal)
    return fyaw % 360.0, fpitch, depth, fwd, right, cam_up


def raycast_bbox_on_splat(
    bbox_norm: Tuple[float, float, float, float],
    cam_pos: np.ndarray,
    depth: np.ndarray,
    fwd: np.ndarray,
    right: np.ndarray,
    cam_up: np.ndarray,
    fw: int,
    fh: int,
    focal: float,
    foreground_pct: float = 50.0,
) -> Optional[List[float]]:
    """Raycast a normalized 2D detection center onto the rendered 3D depth buffer."""
    cx_n = (bbox_norm[0] + bbox_norm[2]) * 0.5
    cy_n = (bbox_norm[1] + bbox_norm[3]) * 0.5
    bx = int(np.clip(cx_n * fw, 0, fw - 1))
    by = int(np.clip(cy_n * fh, 0, fh - 1))
    radius = 6 if foreground_pct < 50.0 else 8
    patch = depth[max(0, by - radius):min(fh, by + radius + 1), max(0, bx - radius):min(fw, bx + radius + 1)]
    valid_d = patch[(patch > 0.30) & (patch < 4.2)]
    if valid_d.size < 4:
        return None
    d_est = float(np.percentile(valid_d, foreground_pct))
    nx = (cx_n - 0.5) * (fw / focal)
    ny = (cy_n - 0.5) * (fh / focal)
    ray = fwd + nx * right - ny * cam_up
    ray /= max(1e-6, float(np.linalg.norm(ray)))
    hit = cam_pos + ray * d_est
    return [round(float(v), 3) for v in hit]


def _encode_crop_b64(bgr: np.ndarray, bbox_xyxy: Tuple[float, float, float, float]) -> str:
    h, w = bgr.shape[:2]
    x1 = max(0, int(bbox_xyxy[0] - 20))
    y1 = max(0, int(bbox_xyxy[1] - 20))
    x2 = min(w, int(bbox_xyxy[2] + 20))
    y2 = min(h, int(bbox_xyxy[3] + 20))
    crop = bgr[y1:y2, x1:x2]
    if crop.size == 0:
        crop = bgr
    scale = min(1.0, 320.0 / max(crop.shape[:2]))
    if scale < 1.0:
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))))
    ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def auto_index_from_images(
    world_points: List[List[float]],
    keyframes: List[Dict[str, Any]],
    read_frame: Callable[[int], Optional[np.ndarray]],
    frame_count: int,
    step: int = 1,
    use_gemini: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Core pipeline: detect ZONES and OBJECTS on keyframes, match true 3D orientation, and cluster."""
    if not world_points or not keyframes or frame_count <= 0:
        return [], []

    yolo = _get_yolo()
    if yolo is None:
        return [], []
    yolo.set_classes(list(ZONE_PROMPTS) + list(OBJECT_PROMPTS))

    stride = max(1, len(world_points) // 325000)
    arr = np.asarray(world_points[::stride], dtype=np.float32)
    xyz = arr[:, :3]
    rgb = np.clip(arr[:, 3:6] * 255.0, 0, 255).astype(np.uint8)

    zone_set = set(ZONE_PROMPTS)
    obj_set = set(OBJECT_PROMPTS)
    zone_hits: List[Dict[str, Any]] = []
    obj_hits: List[Dict[str, Any]] = []
    pose_cache: Dict[int, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, int, float]] = {}

    limit = min(frame_count, len(keyframes))
    indices = list(range(0, limit, max(1, step)))
    for idx in indices:
        bgr = read_frame(idx)
        if bgr is None:
            continue
        oh, ow = bgr.shape[:2]
        res = yolo.predict(bgr, imgsz=640, conf=0.38, verbose=False)[0]
        if not res.boxes:
            continue

        candidates = []
        for b in res.boxes:
            lbl = res.names[int(b.cls)].lower()
            conf = float(b.conf)
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            bn = (x1 / ow, y1 / oh, x2 / ow, y2 / oh)
            area = (bn[2] - bn[0]) * (bn[3] - bn[1])
            if area < 0.004 or area > 0.88:
                continue
            if lbl in zone_set or lbl in obj_set:
                candidates.append((lbl, conf, bn, (x1, y1, x2, y2)))

        if not candidates:
            continue

        if idx not in pose_cache:
            cam_pos = np.array(keyframes[idx]["pose"], dtype=np.float32)
            fw = 180
            fh = max(120, min(320, int(round(fw * (oh / max(1, ow))))))
            focal = round(fw * 1.16, 1)
            _, _, depth, fwd, right, cam_up = match_photo_orientation(bgr, cam_pos, xyz, rgb, fw=fw)
            pose_cache[idx] = (cam_pos, depth, fwd, right, cam_up, fw, fh, focal)

        cam_pos, depth, fwd, right, cam_up, fw, fh, focal = pose_cache[idx]
        for lbl, conf, bn, xyxy in candidates:
            pct = 50.0 if lbl in zone_set else 35.0
            hit = raycast_bbox_on_splat(bn, cam_pos, depth, fwd, right, cam_up, fw, fh, focal, foreground_pct=pct)
            if hit is None:
                continue
            rec = {
                "raw_label": lbl,
                "conf": conf,
                "pos": hit,
                "kf_idx": idx,
                "crop_b64": _encode_crop_b64(bgr, xyxy) if conf >= 0.45 else "",
            }
            if lbl in zone_set:
                zone_hits.append(rec)
            else:
                obj_hits.append(rec)

    sampled = len(indices)
    min_z_hits = 3 if sampled >= 60 else (2 if sampled >= 12 else 1)
    min_o_hits = 3 if sampled >= 60 else (2 if sampled >= 12 else 1)
    zones, objs = cluster_detections_3d(
        zone_hits,
        obj_hits,
        min_zone_hits=min_z_hits,
        min_object_hits=min_o_hits,
        min_zone_conf=0.45 if sampled >= 12 else 0.36,
        min_object_conf=0.50 if sampled >= 12 else 0.36,
    )
    if use_gemini:
        _refine_names_with_gemini(zones, objs)

    for item in zones + objs:
        item.pop("best_crop_b64", None)
    return zones, objs


def auto_index_from_photos(
    world_points: List[List[float]],
    keyframes: List[Dict[str, Any]],
    photos_dir: Path = DEFAULT_ROOM_PHOTOS_DIR,
    step: int = 1,
    use_gemini: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Automatically detect furniture Zones & portable Objects from room photos and 3D point cloud."""
    if not photos_dir.exists():
        return [], []
    files = sorted(
        p for p in photos_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not files:
        return [], []
    return auto_index_from_images(
        world_points=world_points,
        keyframes=keyframes,
        read_frame=lambda idx: cv2.imread(str(files[idx])),
        frame_count=len(files),
        step=step,
        use_gemini=use_gemini,
    )


def auto_index_from_video(
    world_points: List[List[float]],
    keyframes: List[Dict[str, Any]],
    video_path: Path,
    step: int = 1,
    use_gemini: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Automatically detect Zones & Objects from a newly uploaded walkthrough .mp4 video."""
    if not video_path.exists() or not keyframes:
        return [], []
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], []
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total_frames <= 0:
        cap.release()
        return [], []

    def _read_kf_frame(idx: int) -> Optional[np.ndarray]:
        target_f = int(round((idx / max(1, len(keyframes) - 1)) * max(0, total_frames - 1)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_f)
        ok, frame = cap.read()
        return frame if ok else None

    try:
        return auto_index_from_images(
            world_points=world_points,
            keyframes=keyframes,
            read_frame=_read_kf_frame,
            frame_count=len(keyframes),
            step=step,
            use_gemini=use_gemini,
        )
    finally:
        cap.release()


def persist_indexed_world(
    world_id: str,
    db: Any,
    zones: List[Dict[str, Any]],
    objs: List[Dict[str, Any]],
    actor_user_id: str = "usr-conor",
    clear_auto: bool = False,
) -> Dict[str, Any]:
    """Persist automatically discovered Zones and Tracked Objects into the database."""
    now = time.time()
    user = db.get_user(actor_user_id)
    actor_name = user["name"] if user else "Conor"

    if clear_auto:
        for z in db.list_zones(world_id):
            zid = str(z.get("zone_id", ""))
            if zid.startswith("auto-zone-") and hasattr(db, "delete_zone"):
                db.delete_zone(zid)
        for o in db.list_objects(world_id):
            oid = str(o.get("id", ""))
            if oid.startswith("auto-obj-") and hasattr(db, "delete_object"):
                db.delete_object(oid)
        if hasattr(db, "delete_object_events_by_prefix"):
            db.delete_object_events_by_prefix(world_id, "evt-auto-")

    saved_zones: List[Dict[str, Any]] = []
    for idx, z in enumerate(zones):
        pos = z["position"]
        slug = re.sub(r"[^a-z0-9]+", "-", z["label"].lower()).strip("-") or f"zone-{idx}"
        zid = f"auto-zone-{world_id}-{slug}-{idx}"
        zrec = {
            "zone_id": zid,
            "world_id": world_id,
            "name": z["label"],
            "x": float(pos[0]),
            "y": float(pos[1]),
            "z": float(pos[2]),
            "radius_m": 1.25,
            "created_by": actor_user_id,
        }
        db.upsert_zone(zrec)
        saved_zones.append(zrec)

    all_zones = db.list_zones(world_id)
    saved_objs: List[Dict[str, Any]] = []
    for idx, o in enumerate(objs):
        pos = [round(float(v), 3) for v in o["position"][:3]]
        zone_name = resolve_zone(world_id, pos, all_zones, placement_hint="Hotel Room")
        slug = re.sub(r"[^a-z0-9]+", "-", o["label"].lower()).strip("-") or f"obj-{idx}"
        oid = f"auto-obj-{world_id}-{slug}-{idx}"
        orec = {
            "id": oid,
            "label": o["label"],
            "category": "everyday",
            "position": pos,
            "owner": actor_name,
            "owner_id": actor_user_id,
            "last_seen_by_id": actor_user_id,
            "last_seen_by_name": actor_name,
            "last_seen_at": now,
            "zone_name": zone_name,
            "world_id": world_id,
            "audience_type": "group",
            "group_ids": ["grp-room"],
            "timestamp": now,
            "confidence": float(o.get("confidence", 0.7)),
            "seen_by": actor_name,
        }
        db.upsert_object(orec)
        db.append_object_event({
            "event_id": f"evt-auto-{oid}",
            "object_id": oid,
            "label": o["label"],
            "world_id": world_id,
            "event_type": "placed",
            "actor_user_id": actor_user_id,
            "actor_name": actor_name,
            "from_zone": "",
            "from_pos": None,
            "to_zone": zone_name,
            "to_pos": pos,
            "summary": f"{o['label']} detected at {zone_name}",
            "timestamp": now,
        })
        saved_objs.append(orec)

    return {
        "world_id": world_id,
        "zones_count": len(saved_zones),
        "objects_count": len(saved_objs),
        "zones": saved_zones,
        "objects": saved_objs,
    }
