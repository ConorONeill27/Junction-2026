"""
General-Purpose Incremental Walk-Around 3D Reconstructor (`VGGT-1B` on CUDA):
Works on any arbitrary walk-around video without object templates:
1. Motion-Blur Rejection & Diverse Keyframe Extraction (Laplacian sharpness + optical change)
2. Progressive Windowed VGGT-1B Inference (depth + camera poses + per-pixel confidence)
3. Anchor-Linked Sim(3) Alignment across chunks ("fill in what it knows, wait for more info")
4. Confidence-Thresholded & Occlusion-Edge-Stripped Open3D Fusion (clean partial world, no flying sheets)
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import open3d as o3d
import torch

_BACKEND_DIR = Path(__file__).resolve().parent
_VGGT_ROOT = str(_BACKEND_DIR / "vggt_repo")
if _VGGT_ROOT not in sys.path:
    sys.path.insert(0, _VGGT_ROOT)

try:
    import utils3d_moge as utils3d
except ImportError:
    import utils3d  # type: ignore[no-redef]


def _rotation_matrix_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rodrigues 3D rotation matrix rotating unit vector `a` onto unit vector `b`."""
    a = a / max(1e-9, float(np.linalg.norm(a)))
    b = b / max(1e-9, float(np.linalg.norm(b)))
    c = float(np.clip(np.dot(a, b), -1.0, 1.0))
    if c > 0.99999:
        return np.eye(3, dtype=np.float64)
    if c < -0.99999:
        ortho = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        if abs(a[0]) > 0.9:
            ortho = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        axis = np.cross(a, ortho)
        axis /= np.linalg.norm(axis)
        K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]], dtype=np.float64)
        return np.eye(3, dtype=np.float64) + 2.0 * (K @ K)
    v = np.cross(a, b)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]], dtype=np.float64)
    return np.eye(3, dtype=np.float64) + K + (K @ K) / (1.0 + c)


def auto_level_scene(
    pts_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """
    Levels a VGGT point cloud & camera trajectory so the dominant ground/table plane
    is horizontal (normal = +Y) and the primary foreground sits centered above Y = 0.
    """
    if len(pts_np) < 50:
        return pts_np, keyframes

    pts = np.asarray(pts_np, dtype=np.float64).copy()
    cams = np.array([kf["pose"] for kf in keyframes], dtype=np.float64) if keyframes else None
    p10, p90 = np.percentile(pts, 10, axis=0), np.percentile(pts, 90, axis=0)
    span = max(0.3, float(np.linalg.norm(p90 - p10)))

    # Peel up to 3 planes via RANSAC to locate the true horizontal floor/table (within 65° of +Y)
    best_normal: Optional[np.ndarray] = None
    best_inliers_pts: Optional[np.ndarray] = None
    work_pcd = o3d.geometry.PointCloud()
    work_pcd.points = o3d.utility.Vector3dVector(pts)
    min_support = max(60, int(0.12 * len(pts)))

    for _ in range(3):
        if len(work_pcd.points) < min_support:
            break
        pm, inl = work_pcd.segment_plane(
            distance_threshold=max(0.015, 0.025 * span),
            ransac_n=3,
            num_iterations=1200,
        )
        if len(inl) < min_support:
            break
        inlier_pts = np.asarray(work_pcd.points)[inl]
        n = np.asarray(pm[:3], dtype=np.float64)
        n /= max(1e-9, float(np.linalg.norm(n)))

        plane_c = inlier_pts.mean(axis=0)
        if cams is not None and len(cams) > 0:
            if float(np.dot(cams.mean(axis=0) - plane_c, n)) < 0:
                n = -n
        elif float(np.dot(pts.mean(axis=0) - plane_c, n)) < 0:
            n = -n

        if n[1] >= 0.42:  # Ground/table plane rather than vertical 90° wall
            best_normal = n
            best_inliers_pts = inlier_pts
            break
        work_pcd = work_pcd.select_by_index(inl, invert=True)

    y_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    if best_normal is not None:
        R_level = _rotation_matrix_a_to_b(best_normal, y_up)
        pts = pts @ R_level.T
        if cams is not None and len(cams) > 0:
            cams = cams @ R_level.T
        floor_y = float(np.median((best_inliers_pts @ R_level.T)[:, 1]))
    else:
        floor_y = float(np.percentile(pts[:, 1], 4))

    # Re-center XZ around foreground points above floor when available, normalize scale
    p10_l, p90_l = np.percentile(pts, 10, axis=0), np.percentile(pts, 90, axis=0)
    span_l = max(0.3, float(np.linalg.norm(p90_l - p10_l)))
    norm_s = 1.25 / span_l

    above = pts[pts[:, 1] > floor_y + 0.03 * span_l]
    center_xz = np.median(above[:, [0, 2]], axis=0) if len(above) >= 200 else np.median(pts[:, [0, 2]], axis=0)
    origin = np.array([center_xz[0], floor_y, center_xz[1]], dtype=np.float64)

    pts = (pts - origin) * norm_s
    pts[:, 1] += 0.01

    updated_kfs = [dict(kf) for kf in keyframes]
    if cams is not None and len(cams) > 0:
        cams = (cams - origin) * norm_s
        cams[:, 1] += 0.01
        for i, kf in enumerate(updated_kfs):
            kf["pose"] = [round(float(cams[i, 0]), 3), round(float(cams[i, 1]), 3), round(float(cams[i, 2]), 3)]
            kf["yaw"] = round(math.degrees(math.atan2(float(cams[i, 0]), float(cams[i, 2]) + 1e-6)), 1)

    return pts, updated_kfs


def _umeyama_sim3(src: np.ndarray, dst: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
    """Closed-form Umeyama Sim(3): dst ≈ scale * (src @ R.T) + t."""
    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst
    var_src = float(np.sum(src_c * src_c) / max(1, len(src)))
    if var_src < 1e-8:
        return 1.0, np.eye(3, dtype=np.float64), (mu_dst - mu_src)

    cov = (dst_c.T @ src_c) / float(len(src))
    U, S, Vt = np.linalg.svd(cov)
    d = np.ones(3, dtype=np.float64)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        d[2] = -1.0
    R = U @ np.diag(d) @ Vt
    scale = float(np.clip(np.sum(S * d) / var_src, 0.25, 4.0))
    t = mu_dst - scale * (R @ mu_src)
    return scale, R, t


def extract_sharp_keyframes(
    video_path: str,
    out_dir: Path,
    target_segments: int = 28,
    max_keyframes: int = 24,
) -> List[Dict[str, Any]]:
    """
    Single sequential pass over the video using grab() + retrieve() on ~5 candidate frames
    per temporal segment, picking only the sharpest, non-redundant keyframes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    if total <= 0:
        raise ValueError(f"Could not read frames from {video_path}")

    sample_stride = max(1, total // (target_segments * 5))
    seg_len = max(1, total // target_segments)
    seg_best: Dict[int, Tuple[int, float, np.ndarray]] = {}

    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % sample_stride == 0:
            ret, frame = cap.retrieve()
            if ret and frame is not None:
                small = cv2.resize(frame, (320, int(320 * frame.shape[0] / frame.shape[1])))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                seg_i = min(target_segments - 1, idx // seg_len)
                if seg_i not in seg_best or sharp > seg_best[seg_i][1]:
                    # Downscale early to save RAM
                    h, w = frame.shape[:2]
                    sc = min(1.0, 960.0 / max(h, w))
                    if sc < 1.0:
                        frame = cv2.resize(frame, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA)
                    seg_best[seg_i] = (idx, sharp, frame)
        idx += 1
    cap.release()

    candidates = [(s_i, v[0], v[1], v[2]) for s_i, v in sorted(seg_best.items())]
    if not candidates:
        return []

    med_sharp = float(np.median([c[2] for c in candidates]))
    min_sharp = max(12.0, med_sharp * 0.35)

    # Filter blurred segments, keeping best up to max_keyframes
    kept = [c for c in candidates if c[2] >= min_sharp]
    if len(kept) < 6:
        kept = sorted(candidates, key=lambda x: -x[2])[:min(len(candidates), 12)]
        kept = sorted(kept, key=lambda x: x[1])
    elif len(kept) > max_keyframes:
        # Subsample evenly across time
        sel = np.linspace(0, len(kept) - 1, max_keyframes, dtype=int)
        kept = [kept[i] for i in sel]

    saved: List[Dict[str, Any]] = []
    for k, (_, frame_idx, sharp, frame_bgr) in enumerate(kept):
        # Resize to max 1024px height/width for crisp fast disk IO while preserving high detail
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, 960.0 / max(h, w))
        if scale < 1.0:
            frame_bgr = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        fpath = out_dir / f"kf_{k:03d}_idx{frame_idx:04d}.jpg"
        cv2.imwrite(str(fpath), frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 94])
        saved.append({
            "kf_index": k,
            "frame_idx": frame_idx,
            "time_s": round(frame_idx / max(1.0, fps), 2),
            "sharpness": round(sharp, 1),
            "path": str(fpath),
        })
    return saved


class IncrementalVGGTReconstructor:
    """
    Runs Facebook Research's VGGT-1B progressively across overlapping windows
    of frames so new camera angles fuse into the existing partial 3D map.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._device: str = "cuda" if torch.cuda.is_available() else "cpu"

    def _load_model(self) -> None:
        if self._model is not None:
            return
        from vggt.models.vggt import VGGT

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = VGGT.from_pretrained("facebook/VGGT-1B").to(self._device).eval()

    def _infer_window(
        self, image_paths: List[str], conf_percentile: float = 42.0
    ) -> Dict[str, Any]:
        from vggt.utils.load_fn import load_and_preprocess_images
        from vggt.utils.pose_enc import pose_encoding_to_extri_intri
        from vggt.utils.geometry import unproject_depth_map_to_point_map

        self._load_model()
        images = load_and_preprocess_images(image_paths, mode="crop").to(self._device)

        if self._device == "cuda":
            cap_major = torch.cuda.get_device_capability()[0]
            dtype = torch.bfloat16 if cap_major >= 8 else torch.float16
            with torch.inference_mode():
                with torch.amp.autocast("cuda", dtype=dtype):
                    preds = self._model(images)
        else:
            with torch.inference_mode():
                preds = self._model(images)

        extrinsic, intrinsic = pose_encoding_to_extri_intri(
            preds["pose_enc"], images.shape[-2:]
        )

        depth = preds["depth"].float().cpu().numpy().squeeze(0)          # (S, H, W, 1)
        depth_conf = preds["depth_conf"].float().cpu().numpy().squeeze(0)  # (S, H, W)
        extri_np = extrinsic.float().cpu().numpy().squeeze(0)            # (S, 3, 4)
        intri_np = intrinsic.float().cpu().numpy().squeeze(0)            # (S, 3, 3)
        imgs_np = images.float().cpu().numpy()                           # (S, 3, H, W)
        if imgs_np.ndim == 5:
            imgs_np = imgs_np.squeeze(0)
        imgs_rgb = np.transpose(imgs_np, (0, 2, 3, 1))                   # (S, H, W, 3) in [0, 1]

        world_pts = unproject_depth_map_to_point_map(depth, extri_np, intri_np)  # (S, H, W, 3)

        # Clean mask per frame: confidence threshold + occlusion edge stripping
        S, H, W, _ = world_pts.shape
        valid_masks = np.zeros((S, H, W), dtype=bool)
        for s_i in range(S):
            d_i = depth[s_i, :, :, 0]
            c_i = depth_conf[s_i]
            thresh = float(np.percentile(c_i, conf_percentile))
            edge = utils3d.np.depth_map_edge(d_i, rtol=0.045)
            valid_masks[s_i] = (c_i >= max(1.05, thresh)) & (~edge) & (d_i > 0.1) & np.isfinite(d_i)

        # Compute camera centers in world space: C = -R.T @ t
        cam_centers = np.zeros((S, 3), dtype=np.float64)
        for s_i in range(S):
            R_w2c = extri_np[s_i, :3, :3]
            t_w2c = extri_np[s_i, :3, 3]
            cam_centers[s_i] = -(R_w2c.T @ t_w2c)

        if self._device == "cuda":
            torch.cuda.empty_cache()

        return {
            "world_pts": world_pts,
            "depth_conf": depth_conf,
            "valid_masks": valid_masks,
            "imgs_rgb": imgs_rgb,
            "cam_centers": cam_centers,
            "extrinsics": extri_np,
        }

    def reconstruct_video_incremental(
        self,
        video_path: str,
        output_json: Path,
        window_size: int = 12,
        overlap: int = 3,
        max_keyframes: int = 24,
        conf_percentile: float = 42.0,
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Incrementally reconstructs a walk-around video in overlapping windows,
        updating a unified 3D Open3D point cloud as each window resolves.
        """
        t_start = time.time()
        with tempfile.TemporaryDirectory(prefix="vggt_walk_") as tmp:
            kf_dir = Path(tmp) / "keyframes"
            kf_meta = extract_sharp_keyframes(
                video_path, kf_dir, target_segments=max_keyframes + 6, max_keyframes=max_keyframes
            )
            if len(kf_meta) < 2:
                raise RuntimeError("Fewer than 2 sharp keyframes extracted from video")

            paths = [k["path"] for k in kf_meta]
            n_total = len(paths)

            # Build overlapping window slices: [0..W), then [W-overlap .. W-overlap+W), etc.
            windows: List[List[int]] = []
            idx = 0
            while idx < n_total:
                end = min(n_total, idx + window_size)
                win_indices = list(range(idx, end))
                windows.append(win_indices)
                if end == n_total:
                    break
                idx = end - overlap

            global_pcd = o3d.geometry.PointCloud()
            # Map kf_index -> {"world_pts": (H, W, 3), "mask": (H, W), "cam_pos": (3,)}
            resolved_frames: Dict[int, Dict[str, Any]] = {}
            keyframes_out: List[Dict[str, Any]] = []

            for w_i, win_idx in enumerate(windows):
                t_w = time.time()
                win_paths = [paths[k] for k in win_idx]
                res = self._infer_window(win_paths, conf_percentile=conf_percentile)

                # Determine Sim(3) transform from this window to Global World Space
                if w_i == 0:
                    scale, R_align, t_align = 1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64)
                else:
                    # Gather 3D-to-3D anchor points from frames that were already resolved in earlier windows
                    src_samples: List[np.ndarray] = []
                    dst_samples: List[np.ndarray] = []
                    for local_i, kf_i in enumerate(win_idx):
                        if kf_i in resolved_frames:
                            prev_pts = resolved_frames[kf_i]["world_pts"]
                            prev_mask = resolved_frames[kf_i]["mask"]
                            cur_pts = res["world_pts"][local_i]
                            cur_mask = res["valid_masks"][local_i]
                            both = prev_mask & cur_mask
                            if np.any(both):
                                s_pts = cur_pts[both][::4]
                                d_pts = prev_pts[both][::4]
                                src_samples.append(s_pts)
                                dst_samples.append(d_pts)
                    if src_samples:
                        src_cat = np.concatenate(src_samples, axis=0).astype(np.float64)
                        dst_cat = np.concatenate(dst_samples, axis=0).astype(np.float64)
                        if len(src_cat) > 2000:
                            step_s = max(1, len(src_cat) // 2000)
                            src_cat = src_cat[::step_s]
                            dst_cat = dst_cat[::step_s]
                        scale, R_align, t_align = _umeyama_sim3(src_cat, dst_cat)
                    else:
                        scale, R_align, t_align = 1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64)

                # Transform newly resolved frames into Global World Space (OpenGL Three.js Y-up)
                new_pts_accum: List[np.ndarray] = []
                new_cols_accum: List[np.ndarray] = []

                for local_i, kf_i in enumerate(win_idx):
                    pts_loc = res["world_pts"][local_i].astype(np.float64)
                    # Sim(3) align in VGGT OpenCV coords
                    H, W, _ = pts_loc.shape
                    pts_aligned = (scale * (pts_loc.reshape(-1, 3) @ R_align.T) + t_align).reshape(H, W, 3)
                    cam_aligned = scale * (R_align @ res["cam_centers"][local_i]) + t_align

                    is_new_frame = kf_i not in resolved_frames
                    resolved_frames[kf_i] = {
                        "world_pts": pts_aligned,
                        "mask": res["valid_masks"][local_i],
                        "cam_pos": cam_aligned,
                    }

                    if is_new_frame:
                        mask = res["valid_masks"][local_i]
                        if np.any(mask):
                            # OpenCV (+X right, +Y down, +Z forward) -> Three.js (+X right, -Y up, -Z forward)
                            pts_gl = pts_aligned[mask] * np.array([1.0, -1.0, -1.0], dtype=np.float64)
                            cols_gl = res["imgs_rgb"][local_i][mask].astype(np.float64)
                            new_pts_accum.append(pts_gl[::2])
                            new_cols_accum.append(cols_gl[::2])

                        cam_gl = cam_aligned * np.array([1.0, -1.0, -1.0], dtype=np.float64)
                        keyframes_out.append({
                            "index": kf_i + 1,
                            "pose": [round(float(c), 3) for c in cam_gl],
                            "yaw": round(math.degrees(math.atan2(float(cam_gl[0]), float(cam_gl[2]) + 1e-6)), 1),
                            "pitch": 15.0,
                            "inliers": int(np.sum(mask)),
                            "points": int(np.sum(mask) // 2),
                            "mode": "vggt-walkthrough",
                            "time_s": kf_meta[kf_i]["time_s"],
                        })

                if new_pts_accum:
                    win_pcd = o3d.geometry.PointCloud()
                    win_pcd.points = o3d.utility.Vector3dVector(np.concatenate(new_pts_accum, axis=0))
                    win_pcd.colors = o3d.utility.Vector3dVector(np.concatenate(new_cols_accum, axis=0))
                    win_pcd = win_pcd.voxel_down_sample(voxel_size=0.008)
                    global_pcd = global_pcd + win_pcd
                    global_pcd = global_pcd.voxel_down_sample(voxel_size=0.009)

                msg = f"Window {w_i+1}/{len(windows)} done in {time.time()-t_w:.2f}s -> {len(global_pcd.points):,} pts"
                print(msg)
                if progress_cb:
                    progress_cb(msg, len(global_pcd.points))

            # Final outlier denoising & normalization (center scene around X=0, Z=0, base near Y=0.05)
            if len(global_pcd.points) >= 100:
                global_pcd, _ = global_pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=1.6)

            pts_np = np.asarray(global_pcd.points)
            cols_np = np.asarray(global_pcd.colors)

            if len(pts_np) > 0:
                pts_np, keyframes_out = auto_level_scene(pts_np, keyframes_out)

            # Cap to 65,000 points for smooth 60fps WebGL rendering
            if len(pts_np) > 65000:
                sel = np.linspace(0, len(pts_np) - 1, 65000, dtype=int)
                pts_np = pts_np[sel]
                cols_np = cols_np[sel]

            points_list = [
                [
                    round(float(pts_np[i, 0]), 4),
                    round(float(pts_np[i, 1]), 4),
                    round(float(pts_np[i, 2]), 4),
                    round(float(cols_np[i, 0]), 3),
                    round(float(cols_np[i, 1]), 3),
                    round(float(cols_np[i, 2]), 3),
                ]
                for i in range(len(pts_np))
            ]

            ply_out = output_json.with_suffix(".ply")
            out_pcd = o3d.geometry.PointCloud()
            out_pcd.points = o3d.utility.Vector3dVector(pts_np)
            out_pcd.colors = o3d.utility.Vector3dVector(cols_np)
            o3d.io.write_point_cloud(str(ply_out), out_pcd)

            elapsed = round(time.time() - t_start, 2)
            payload = {
                "points": points_list,
                "keyframes": keyframes_out,
                "elapsed_s": elapsed,
                "device": self._device,
                "windows": len(windows),
            }
            output_json.parent.mkdir(parents=True, exist_ok=True)
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(payload, f)

            print(
                f"Incremental VGGT-1B complete in {elapsed}s on {self._device}: "
                f"{len(keyframes_out)} sharp frames -> {len(points_list):,} cleaned 3D points"
            )
            return payload


if __name__ == "__main__":
    vid = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\conor\Downloads\PXL_20261113_223120985.mp4"
    out_file = _BACKEND_DIR / "static" / "vggt_walkthrough_points.json"
    recon = IncrementalVGGTReconstructor()
    recon.reconstruct_video_incremental(vid, out_file, window_size=12, overlap=3, max_keyframes=21)
