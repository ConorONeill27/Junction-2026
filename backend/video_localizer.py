"""Localize a walkthrough/glasses video + detected objects onto the 3D VGGT point cloud."""

from pathlib import Path
import math
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOM_PHOTOS_DIR = PROJECT_ROOT / "captured_media" / "room_photos"
DEFAULT_VIDEO_PATH = PROJECT_ROOT / "captured_media" / "videos" / "PXL_20261114_202007792.mp4"
YOLO_WEIGHTS = Path(__file__).resolve().parent / "yolo_world" / "yolov8s-worldv2.pt"

_REF_CACHE: Dict[str, List[Tuple[int, Any, Any, int, int, str]]] = {}
_YOLO_MODEL = None


def _get_sift():
    return cv2.SIFT_create(nfeatures=650)


def _get_ref_features(photos_dir: Path) -> List[Tuple[int, Any, Any, int, int, str]]:
    key = str(photos_dir.resolve())
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    sift = _get_sift()
    feats = []
    files = sorted(
        p for p in photos_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    for idx, p in enumerate(files):
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        img_s = cv2.resize(img, (480, max(1, int(h * 480 / w))))
        kp, des = sift.detectAndCompute(cv2.cvtColor(img_s, cv2.COLOR_BGR2GRAY), None)
        feats.append((idx, kp, des, img_s.shape[1], img_s.shape[0], p.name))
    _REF_CACHE[key] = feats
    return feats


def _get_yolo():
    global _YOLO_MODEL
    if _YOLO_MODEL is not None:
        return _YOLO_MODEL
    if not YOLO_WEIGHTS.exists():
        return None
    from ultralytics import YOLOWorld
    import torch

    model = YOLOWorld(str(YOLO_WEIGHTS))
    if torch.cuda.is_available():
        model.to("cuda:0")
    model.set_classes([
        "water bottle", "bottle", "thermos", "backpack", "bag",
        "keys", "wallet", "phone", "laptop", "bed", "nightstand",
        "desk", "chair", "door", "person",
    ])
    _YOLO_MODEL = model
    return _YOLO_MODEL


def _match_frame_to_keyframes(
    bgr: np.ndarray,
    ref_feats: List[Tuple[int, Any, Any, int, int, str]],
    keyframes: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    h, w = bgr.shape[:2]
    qf = cv2.resize(bgr, (480, max(1, int(h * 480 / w))))
    sift = _get_sift()
    kp1, des1 = sift.detectAndCompute(cv2.cvtColor(qf, cv2.COLOR_BGR2GRAY), None)
    if des1 is None or len(des1) < 12:
        return None

    bf = cv2.BFMatcher()
    best_inliers = 0
    best_idx = -1
    best_name = ""
    for idx, kp2, des2, _rw, _rh, name in ref_feats:
        if idx >= len(keyframes) or des2 is None or len(des2) < 10:
            continue
        matches = bf.knnMatch(des1, des2, k=2)
        good = [m for pair in matches if len(pair) == 2 for m, n in [pair] if m.distance < 0.72 * n.distance]
        if len(good) < 8:
            continue
        src = np.float32([kp1[m.queryIdx].pt for m in good])
        dst = np.float32([kp2[m.trainIdx].pt for m in good])
        _H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 12.0)
        inliers = int(mask.sum()) if mask is not None else 0
        if inliers > best_inliers:
            best_inliers = inliers
            best_idx = idx
            best_name = name

    if best_idx < 0 or best_inliers < 8:
        return None
    return {
        "kf_index": best_idx,
        "photo": best_name,
        "inliers": best_inliers,
        "pose": list(keyframes[best_idx]["pose"]),
    }


def _render_splat(
    xyz: np.ndarray,
    rgb: np.ndarray,
    cam_pos: np.ndarray,
    yaw_deg: float,
    pitch_deg: float,
    fw: int = 180,
    fh: int = 320,
    focal: float = 260.0,
):
    rel = xyz - cam_pos[None, :]
    yr = math.radians(yaw_deg)
    pr = math.radians(pitch_deg)
    fwd = np.array(
        [math.sin(yr) * math.cos(pr), math.sin(pr), -math.cos(yr) * math.cos(pr)],
        dtype=np.float32,
    )
    up_w = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    right = np.cross(fwd, up_w)
    right /= max(1e-6, float(np.linalg.norm(right)))
    cam_up = np.cross(right, fwd)

    x_c = rel @ right
    y_c = rel @ cam_up
    z_c = rel @ fwd
    mask = (z_c > 0.25) & (z_c < 5.5)
    u = (focal * (x_c[mask] / z_c[mask]) + fw * 0.5).astype(int)
    v = (-focal * (y_c[mask] / z_c[mask]) + fh * 0.5).astype(int)
    valid = (u >= 0) & (u < fw) & (v >= 0) & (v < fh)

    img = np.zeros((fh, fw, 3), dtype=np.uint8)
    depth = np.full((fh, fw), 99.0, dtype=np.float32)
    uu, vv, zz, cc = u[valid], v[valid], z_c[mask][valid], rgb[mask][valid]
    order = np.argsort(-zz)
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            iu = np.clip(uu[order] + ox, 0, fw - 1)
            iv = np.clip(vv[order] + oy, 0, fh - 1)
            img[iv, iu] = cc[order][:, ::-1]
            depth[iv, iu] = zz[order]
    return img, depth, fwd, right, cam_up


def _estimate_pose_and_raycast(
    bgr: np.ndarray,
    cam_pos: np.ndarray,
    xyz: np.ndarray,
    rgb: np.ndarray,
    bbox_norm: Tuple[float, float, float, float],
) -> Tuple[List[float], float, float]:
    """Refine 3D camera yaw/pitch via density-normalized LAB tile similarity and raycast bbox center onto cloud."""
    fw, fh, focal = 180, 320, 260.0
    q_bgr = cv2.GaussianBlur(cv2.resize(bgr, (fw, fh)), (9, 9), 0)
    q_lab = cv2.cvtColor(q_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    def _tile_loss(yaw_deg: float, pitch_deg: float) -> float:
        img, _, _, _, _ = _render_splat(xyz, rgb, cam_pos, yaw_deg, pitch_deg, fw=fw, fh=fh, focal=focal)
        m = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 0).astype(np.float32)
        bm = cv2.GaussianBlur(m, (9, 9), 0)
        vm = bm > 0.15
        if vm.sum() < 10000:
            return 999.0
        bimg = cv2.GaussianBlur(img.astype(np.float32) * m[:, :, None], (9, 9), 0) / np.maximum(1e-3, bm[:, :, None])
        r_lab = cv2.cvtColor(np.clip(bimg, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
        losses = []
        for ty in range(0, fh, 40):
            for tx in range(0, fw, 45):
                tm = vm[ty:ty + 40, tx:tx + 45]
                if tm.sum() < 200:
                    continue
                d = np.abs(r_lab[ty:ty + 40, tx:tx + 45][tm] - q_lab[ty:ty + 40, tx:tx + 45][tm])
                losses.append(float((d[:, 0] + 1.5 * d[:, 1] + 1.5 * d[:, 2]).mean()))
        return float(np.mean(losses)) if losses else 999.0

    best = (999.0, 0.0, -18.0)
    for yaw in range(0, 360, 15):
        loss = _tile_loss(float(yaw), -18.0)
        if loss < best[0]:
            best = (loss, float(yaw), -18.0)

    _, cyaw, _ = best
    for pitch in (-24.0, -18.0, -12.0):
        for dy in (-10, -5, 0, 5, 10):
            loss = _tile_loss(cyaw + dy, pitch)
            if loss < best[0]:
                best = (loss, cyaw + dy, pitch)

    _, fyaw, fpitch = best
    _, depth, fwd, right, cam_up = _render_splat(xyz, rgb, cam_pos, fyaw, fpitch, fw=fw, fh=fh, focal=focal)
    cx_n = (bbox_norm[0] + bbox_norm[2]) * 0.5
    cy_n = (bbox_norm[1] + bbox_norm[3]) * 0.5
    bx = int(np.clip(cx_n * fw, 0, fw - 1))
    by = int(np.clip(cy_n * fh, 0, fh - 1))
    patch = depth[max(0, by - 9):min(fh, by + 10), max(0, bx - 9):min(fw, bx + 10)]
    valid_d = patch[patch < 50.0]
    d_est = float(np.median(valid_d)) if valid_d.size > 0 else 1.25

    nx = (cx_n - 0.5) * (fw / focal)
    ny = (cy_n - 0.5) * (fh / focal)
    ray = fwd + nx * right - ny * cam_up
    ray /= max(1e-6, float(np.linalg.norm(ray)))
    hit_3d = cam_pos + ray * d_est
    return [round(float(v), 3) for v in hit_3d], round(fyaw % 360.0, 1), round(fpitch, 1)


def localize_video_on_world(
    video_path: Optional[Path],
    world_points: List[List[float]],
    keyframes: List[Dict[str, Any]],
    photos_dir: Path = DEFAULT_ROOM_PHOTOS_DIR,
    target_labels: Tuple[str, ...] = ("water bottle", "bottle"),
) -> Dict[str, Any]:
    """Localize walking trajectory & left-behind target object from a video inside the 3D point cloud."""
    vid = Path(video_path) if video_path else DEFAULT_VIDEO_PATH
    if not vid.exists():
        return {"ok": False, "error": f"Video not found: {vid}"}
    if not photos_dir.exists() or not keyframes or not world_points:
        return {"ok": False, "error": "3D world keyframes or reference room photos not available"}

    cap = cv2.VideoCapture(str(vid))
    if not cap.isOpened():
        return {"ok": False, "error": f"Cannot open video: {vid}"}

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / max(1.0, fps)

    ref_feats = _get_ref_features(photos_dir)
    yolo = _get_yolo()

    step_frames = max(1, int(round(fps * 0.25)))
    trail_every = 8  # every 2.0s along the walk
    trail: List[Dict[str, Any]] = []
    target_candidates = []  # (base_score, t, frame_bgr, bbox_norm, conf, det_label)

    frame_idx = 0
    sample_idx = 0
    while True:
        if not cap.grab():
            break
        if frame_idx % step_frames == 0:
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                frame_idx += 1
                continue
            t = round(frame_idx / max(1.0, fps), 2)
            fh, fw = frame.shape[:2]

            if yolo is not None:
                res = yolo.predict(frame, imgsz=960, conf=0.30, verbose=False)[0]
                people_boxes = []
                furniture_present = False
                for b in res.boxes:
                    lname = res.names[int(b.cls)].lower()
                    if lname == "person":
                        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
                        people_boxes.append((x1 / fw, y1 / fh, x2 / fw, y2 / fh))
                    elif lname in ("bed", "nightstand", "desk", "chair"):
                        furniture_present = True
                for b in res.boxes:
                    lbl = res.names[int(b.cls)].lower()
                    conf = float(b.conf)
                    if lbl not in target_labels:
                        continue
                    x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
                    bn = (x1 / fw, y1 / fh, x2 / fw, y2 / fh)
                    area = (bn[2] - bn[0]) * (bn[3] - bn[1])
                    if area > 0.12 or bn[3] > 0.92 or bn[2] > 0.96:
                        continue
                    # Prefer small unattended surface/floor sightings near identifiable room furniture
                    score = conf
                    if area < 0.04:
                        score += 0.35
                    if not people_boxes:
                        score += 0.25
                    if furniture_present:
                        score += 0.20
                    target_candidates.append((score, t, frame.copy(), bn, conf, lbl))

            if sample_idx % trail_every == 0:
                loc = _match_frame_to_keyframes(frame, ref_feats, keyframes)
                if loc is not None:
                    trail.append({
                        "t": t,
                        "kf_index": loc["kf_index"],
                        "inliers": loc["inliers"],
                        "pose": [round(float(v), 3) for v in loc["pose"]],
                    })
            sample_idx += 1
        frame_idx += 1

    cap.release()

    if not target_candidates:
        return {"ok": False, "error": "Target item not detected unattended in video", "video_trail": trail}

    # Jointly rank visual detections by SIFT geometric anchor strength so we raycast from a well-localized frame
    best_joint = None
    best_target = None
    target_loc = None
    for base_score, t, cand_frame, bn, conf, lbl in sorted(target_candidates, key=lambda x: x[0], reverse=True)[:12]:
        loc = _match_frame_to_keyframes(cand_frame, ref_feats, keyframes)
        if loc is None and trail:
            nearest = min(trail, key=lambda x: abs(x["t"] - t))
            loc = {"kf_index": nearest["kf_index"], "inliers": max(1, nearest["inliers"] // 2), "pose": nearest["pose"]}
        if loc is None:
            continue
        joint_score = base_score + min(loc["inliers"], 35) / 35.0 * 0.65
        if best_joint is None or joint_score > best_joint:
            best_joint = joint_score
            best_target = (base_score, t, cand_frame, bn, conf, lbl)
            target_loc = loc

    if best_target is None or target_loc is None:
        return {"ok": False, "error": "Could not match target video frame to 3D room map", "video_trail": trail}

    _, target_t, target_frame, target_bn, target_conf, target_lbl = best_target

    stride = max(1, len(world_points) // 325000)
    arr = np.asarray(world_points[::stride], dtype=np.float32)
    xyz = arr[:, :3]
    rgb = np.clip(arr[:, 3:6] * 255.0, 0, 255).astype(np.uint8)
    cam_pos = np.array(target_loc["pose"], dtype=np.float32)

    hit_3d, yaw_deg, pitch_deg = _estimate_pose_and_raycast(target_frame, cam_pos, xyz, rgb, target_bn)

    # Ensure target observation pose is represented cleanly in the trail
    if not any(abs(p["t"] - target_t) < 0.6 for p in trail):
        trail.append({
            "t": target_t,
            "kf_index": target_loc["kf_index"],
            "inliers": target_loc["inliers"],
            "pose": [round(float(v), 3) for v in cam_pos],
            "is_target_view": True,
        })
        trail.sort(key=lambda p: p["t"])

    return {
        "ok": True,
        "video": vid.name,
        "target_label": "Water Bottle" if "bottle" in target_lbl else target_lbl.title(),
        "target_t": target_t,
        "confidence": round(target_conf, 3),
        "camera_pose": [round(float(v), 3) for v in cam_pos],
        "camera_yaw": yaw_deg,
        "camera_pitch": pitch_deg,
        "target_position": hit_3d,
        "matched_keyframe": target_loc["kf_index"],
        "sift_inliers": target_loc["inliers"],
        "video_trail": trail,
    }
