"""
Neural 3D Spatial Scene Understanding, Dot Realignment & Room Surface Completion
-------------------------------------------------------------------------------
Uses Mask2Former (Swin-Large ADE20K) per-pixel indoor semantics aligned 1-to-1 with
VGGT-1B 3D point maps to:
1. Understand every 3D point semantically (floor, wall, ceiling, door, glass/window, objects, person).
2. Realign per-frame camera depth scale & residual horizon pitch so multi-clip floors/walls
   snap cleanly onto a single crisp plane (eliminating double-floor slabs & drift).
3. Register multi-clip sub-clouds via gravity-locked Open3D Point-to-Plane ICP.
4. Remove reflection/through-glass ghost points, people/body artifacts, and below-floor specular noise.
5. Solve clean orthogonal walls, floor, ceiling, and doorway openings from true architecture.
6. Synthesize color-matched 3D surface completions for unobserved floor, upper/closure walls, and ceiling.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import open3d as o3d
from PIL import Image
from scipy.spatial import cKDTree
import torch

from walkthrough_vggt import (
    _extract_dominant_room_yaw,
    _rotation_matrix_a_to_b,
    _snap_aligned_polygon,
)

# Unified semantic classes
SEM_IGNORE = 0
SEM_FLOOR = 1
SEM_WALL = 2
SEM_CEILING = 3
SEM_DOOR = 4
SEM_GLASS = 5
SEM_OBJECT = 6


class SemanticSpatialModel:
    """Lazy-loaded Mask2Former-Swin-Large ADE20K parser + 3D spatial solver."""

    MODEL_ID = "facebook/mask2former-swin-large-ade-semantic"

    def __init__(self, device: Optional[str] = None) -> None:
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._processor = None
        self._model = None
        self._lut: Optional[np.ndarray] = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation

        print(f"[SpatialModel] Loading {self.MODEL_ID} on {self._device}...")
        self._processor = AutoImageProcessor.from_pretrained(self.MODEL_ID)
        dtype = torch.float16 if self._device == "cuda" else torch.float32
        self._model = (
            Mask2FormerForUniversalSegmentation.from_pretrained(self.MODEL_ID, torch_dtype=dtype)
            .to(self._device)
            .eval()
        )
        id2label: Dict[int, str] = self._model.config.id2label
        max_id = max(int(k) for k in id2label.keys()) + 1
        lut = np.full(max_id, SEM_OBJECT, dtype=np.uint8)
        for k, raw_lbl in id2label.items():
            idx = int(k)
            l = str(raw_lbl).lower()
            if "person" in l:
                lut[idx] = SEM_IGNORE
            elif any(w in l for w in ["floor", "rug", "carpet", "mat"]):
                lut[idx] = SEM_FLOOR
            elif any(w in l for w in ["wall", "column", "pillar"]):
                lut[idx] = SEM_WALL
            elif "ceiling" in l:
                lut[idx] = SEM_CEILING
            elif "door" in l:
                lut[idx] = SEM_DOOR
            elif any(w in l for w in ["window", "glass", "mirror"]):
                lut[idx] = SEM_GLASS
            else:
                lut[idx] = SEM_OBJECT
        self._lut = lut

    def unload(self) -> None:
        if self._model is not None:
            del self._model, self._processor
            self._model = None
            self._processor = None
            if self._device == "cuda":
                torch.cuda.empty_cache()

    def segment_frames(self, imgs_rgb: np.ndarray) -> np.ndarray:
        """
        Segments `(S, H, W, 3)` float [0..1] frames into `(S, H, W)` uint8 `SEM_*` labels
        with exact 1-to-1 pixel correspondence to VGGT `world_pts`.
        """
        self._ensure_loaded()
        assert self._processor is not None and self._model is not None and self._lut is not None
        S, H, W = imgs_rgb.shape[:3]
        sem_out = np.zeros((S, H, W), dtype=np.uint8)
        lut = self._lut

        for s_i in range(S):
            im_u8 = (np.clip(imgs_rgb[s_i], 0.0, 1.0) * 255.0).astype(np.uint8)
            pil_im = Image.fromarray(im_u8)
            inputs = self._processor(images=pil_im, return_tensors="pt").to(self._device)
            if self._device == "cuda":
                inputs = {k: (v.half() if v.dtype == torch.float32 else v) for k, v in inputs.items()}
            with torch.inference_mode():
                outputs = self._model(**inputs)
            raw_seg = (
                self._processor.post_process_semantic_segmentation(outputs, target_sizes=[(H, W)])[0]
                .cpu()
                .numpy()
            )
            mapped = lut[np.clip(raw_seg, 0, len(lut) - 1)]
            sem_out[s_i] = mapped

        return sem_out


def _level_and_scale_semantic(
    wpts: np.ndarray,
    cols: np.ndarray,
    masks: np.ndarray,
    sem: np.ndarray,
    cams: np.ndarray,
    cam_ups: np.ndarray,
    target_cam_height_m: float = 1.24,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Rotates VGGT frames onto true floor-up Y axis using SEM_FLOOR points and recovers
    consistent physical metric scale (~1.24m camera walking height above floor).
    Returns (wpts_m, cams_m, R_total, metric_scale).
    """
    y_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    R0 = np.eye(3, dtype=np.float64)
    if len(cam_ups) > 0:
        avg_up = np.median(cam_ups, axis=0)
        n_up = float(np.linalg.norm(avg_up))
        if n_up > 1e-6:
            R0 = _rotation_matrix_a_to_b(avg_up / n_up, y_up)
            wpts = wpts @ R0.T
            cams = cams @ R0.T

    m_floor = masks & (sem == SEM_FLOOR)
    fl_pts = wpts[m_floor]
    if len(fl_pts) < 250:
        fl_pts = wpts[masks]

    R_lvl = np.eye(3, dtype=np.float64)
    if len(fl_pts) >= 250:
        step = max(1, len(fl_pts) // 25000)
        fpcd = o3d.geometry.PointCloud()
        fpcd.points = o3d.utility.Vector3dVector(fl_pts[::step])
        span_est = max(0.3, float(np.linalg.norm(np.percentile(fl_pts[::step], 90, axis=0) - np.percentile(fl_pts[::step], 10, axis=0))))
        pm, inl = fpcd.segment_plane(
            distance_threshold=max(0.012, 0.014 * span_est),
            ransac_n=3,
            num_iterations=1200,
        )
        if len(inl) >= 80:
            n_fl = np.asarray(pm[:3], dtype=np.float64)
            n_fl /= max(1e-9, float(np.linalg.norm(n_fl)))
            if n_fl[1] < 0:
                n_fl = -n_fl
            if n_fl[1] >= 0.85:
                R_lvl = _rotation_matrix_a_to_b(n_fl, y_up)
                wpts = wpts @ R_lvl.T
                cams = cams @ R_lvl.T

    fl_y0 = float(np.median(wpts[m_floor, 1])) if np.any(m_floor) else float(np.percentile(wpts[masks, 1], 4))
    wpts[..., 1] -= fl_y0
    cams[:, 1] -= fl_y0

    cam_h_med = float(np.median(cams[:, 1])) if len(cams) > 0 else 0.4
    metric_s = target_cam_height_m / float(np.clip(cam_h_med, 0.08, 3.0))
    # Sanity clamp metric scale so odd stationary scans stay inside realistic meter range
    all_pts = wpts[masks]
    if len(all_pts) > 50:
        raw_span = max(0.2, float(np.linalg.norm(np.percentile(all_pts, 90, axis=0) - np.percentile(all_pts, 10, axis=0))))
        span_m = raw_span * metric_s
        if span_m > 28.0:
            metric_s = 28.0 / raw_span
        elif span_m < 1.8:
            metric_s = 1.8 / raw_span

    return wpts * metric_s, cams * metric_s, R_lvl @ R0, float(metric_s)


def realign_frames_semantically(
    wpts: np.ndarray,
    masks: np.ndarray,
    sem: np.ndarray,
    cams: np.ndarray,
) -> Tuple[np.ndarray, int]:
    """
    Realigns each frame's 3D points around its camera center `C_i`:
    - Fixes residual horizon pitch/roll drift (<4 deg) from semantic FLOOR plane normal.
    - Scales ray depths by `alpha_i` so each frame's semantic floor lands flush at y = 0.0m,
      collapsing multi-layer floor slabs and seating walls/objects cleanly on the same floor.
    """
    S = wpts.shape[0]
    aligned = wpts.copy()
    y_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    realigned_cnt = 0

    for s_i in range(S):
        mf = masks[s_i] & (sem[s_i] == SEM_FLOOR)
        n_fl = int(np.count_nonzero(mf))
        if n_fl < 200:
            continue
        ci = cams[s_i]
        pf = aligned[s_i][mf]
        # Ignore floor points right at camera height
        below = pf[:, 1] < (ci[1] - 0.18)
        if int(np.count_nonzero(below)) < 160:
            continue
        pf_b = pf[below]

        spread_pre = float(np.percentile(pf_b[:, 1], 90) - np.percentile(pf_b[:, 1], 10))
        if spread_pre > 0.28:
            continue

        # Optional small floor tilt correction around camera center C_i if floor covers wide XZ footprint
        xz_span = float(np.linalg.norm(np.percentile(pf_b[:, [0, 2]], 90, axis=0) - np.percentile(pf_b[:, [0, 2]], 10, axis=0)))
        R_tilt = np.eye(3, dtype=np.float64)
        if n_fl >= 450 and xz_span >= 0.70:
            fpcd = o3d.geometry.PointCloud()
            step = max(1, len(pf_b) // 4000)
            fpcd.points = o3d.utility.Vector3dVector(pf_b[::step])
            pm, inl = fpcd.segment_plane(distance_threshold=0.025, ransac_n=3, num_iterations=400)
            if len(inl) >= max(100, int(0.65 * (len(pf_b) // step))):
                n_loc = np.asarray(pm[:3], dtype=np.float64)
                n_loc /= max(1e-9, float(np.linalg.norm(n_loc)))
                if n_loc[1] < 0:
                    n_loc = -n_loc
                ang_deg = math.degrees(math.acos(float(np.clip(n_loc[1], -1.0, 1.0))))
                if 0.15 <= ang_deg <= 4.0:
                    R_cand = _rotation_matrix_a_to_b(n_loc, y_up)
                    pf_rot = ci + (pf_b - ci) @ R_cand.T
                    spread_rot = float(np.percentile(pf_rot[:, 1], 90) - np.percentile(pf_rot[:, 1], 10))
                    if spread_rot <= spread_pre + 0.005:
                        R_tilt = R_cand
                        pf_b = pf_rot
                        spread_pre = spread_rot

        # Ray depth scale so frame's semantic floor sits on y = 0.0m
        alphas = ci[1] / np.maximum(0.15, ci[1] - pf_b[:, 1])
        a_med = float(np.median(alphas))
        inl_mask = np.abs(alphas - a_med) <= 0.08
        if float(np.mean(inl_mask)) < 0.70:
            continue
        alpha_i = float(np.clip(np.median(alphas[inl_mask]), 0.86, 1.14))
        pf_post = ci + alpha_i * (pf_b - ci)
        spread_post = float(np.percentile(pf_post[:, 1], 90) - np.percentile(pf_post[:, 1], 10))
        if spread_post <= min(0.16, spread_pre + 0.02):
            aligned[s_i] = ci + alpha_i * ((aligned[s_i] - ci) @ R_tilt.T)
            realigned_cnt += 1

    return aligned, realigned_cnt


def _dominant_wall_axis_deg(pts_xz: np.ndarray) -> float:
    """
    Finds the dominant orthogonal corridor/room yaw (deg in [-45, +45]) by voxelizing
    wall points spatially (preventing single close-up frames from outvoting long walls)
    and maximizing Manhattan 1D histogram projection sharpness.
    """
    if len(pts_xz) < 30:
        return 0.0
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(
        np.column_stack([pts_xz[:, 0], np.zeros(len(pts_xz)), pts_xz[:, 1]])
    )
    pcd = pcd.voxel_down_sample(voxel_size=0.06)
    vx = np.asarray(pcd.points)[:, [0, 2]]
    if len(vx) < 20:
        return 0.0

    best_deg, best_sharp = 0.0, -1.0
    for deg in np.linspace(-45.0, 44.5, 180):
        th = math.radians(deg)
        c, s = math.cos(th), math.sin(th)
        u = vx[:, 0] * c + vx[:, 1] * s
        v = -vx[:, 0] * s + vx[:, 1] * c
        hu, _ = np.histogram(u, bins=np.arange(u.min() - 0.1, u.max() + 0.2, 0.07))
        hv, _ = np.histogram(v, bins=np.arange(v.min() - 0.1, v.max() + 0.2, 0.07))
        sharp = float(np.sum(hu.astype(np.float64) ** 2) + np.sum(hv.astype(np.float64) ** 2))
        if sharp > best_sharp:
            best_sharp = sharp
            best_deg = deg
    return float(best_deg)


def apply_imu_constraints(
    wpts: np.ndarray,
    cams: np.ndarray,
    cam_fwds_gl: Optional[np.ndarray],
    imu_kf: Optional[List[Optional[Dict[str, Any]]]],
    clip_ids_per_frame: Optional[List[str]] = None,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Fuses synchronized 9-axis glasses IMU priors into VGGT 3D geometry:
    1. Anti-bend gyro yaw straightening: cancels cumulative monocular visual yaw drift
       across long texture-poor corridors around each camera center `C_i`.
    2. Accelerometer step-cadence metric scale anchor: calibrates corridor distance
       using real step displacement (`~0.72 m/step`) alongside camera height scale.
    3. Records per-clip compass North yaw anchor for multi-clip alignment and live HUD arrows.
    """
    meta: Dict[str, Any] = {"imu_used": False}
    S = len(wpts)
    if not imu_kf or S < 3 or len(imu_kf) < S:
        return wpts, cams, meta

    out_pts = wpts.copy()
    out_cams = cams.copy()

    # Work per clip so distinct clips maintain their own gyro baseline before clip ICP
    groups: List[List[int]] = []
    if clip_ids_per_frame and len(clip_ids_per_frame) == S:
        seen_cid: Dict[str, List[int]] = {}
        for i, cid in enumerate(clip_ids_per_frame):
            seen_cid.setdefault(cid, []).append(i)
        groups = list(seen_cid.values())
    else:
        groups = [list(range(S))]

    compass_offsets: List[float] = []
    total_steps_all = 0.0

    for idxs in groups:
        valid_i = [i for i in idxs if imu_kf[i] is not None and "gyro_yaw_deg" in imu_kf[i]]
        if len(valid_i) < 3:
            continue
        meta["imu_used"] = True

        # 1. Gyro vs VGGT visual yaw straightening
        g_raw = np.array([float(imu_kf[i]["gyro_yaw_deg"]) for i in valid_i], dtype=np.float64)
        g_rel = g_raw - g_raw[0]
        if cam_fwds_gl is not None and len(cam_fwds_gl) == S:
            fwds = np.asarray(cam_fwds_gl[valid_i], dtype=np.float64)
            vis_yaw = np.rad2deg(np.unwrap(np.arctan2(fwds[:, 0], -fwds[:, 2])))
            vis_rel = vis_yaw - vis_yaw[0]

            # Auto-align gyro sign convention (+/-) using coherent turn variation
            if float(np.std(g_rel)) > 2.0 and float(np.std(vis_rel)) > 2.0:
                corr = float(np.corrcoef(vis_rel, g_rel)[0, 1])
                if corr < -0.25:
                    g_rel = -g_rel

            yaw_err = g_rel - vis_rel
            # Apply smooth anti-bend correction only for realistic monocular yaw drift (< 24 deg)
            if 1.2 <= float(np.max(np.abs(yaw_err))) <= 24.0:
                # Re-rotate each frame's points cleanly around its camera center (clockwise around +Y matches atan2(x, -z))
                for k_pos, s_i in enumerate(valid_i):
                    d_rad = math.radians(float(yaw_err[k_pos]))
                    c_d, s_d = math.cos(d_rad), math.sin(d_rad)
                    R_yaw = np.array([[c_d, 0.0, -s_d], [0.0, 1.0, 0.0], [s_d, 0.0, c_d]], dtype=np.float64)
                    ci = out_cams[s_i]
                    out_pts[s_i] = ci + (out_pts[s_i] - ci) @ R_yaw.T

                # Also straighten camera centers smoothly around first frame center
                c0 = out_cams[valid_i[0]].copy()
                rebuilt_cams = [c0]
                for k_pos in range(1, len(valid_i)):
                    i_prev, i_cur = valid_i[k_pos - 1], valid_i[k_pos]
                    step_vec = cams[i_cur] - cams[i_prev]
                    mid_err = 0.5 * (yaw_err[k_pos - 1] + yaw_err[k_pos])
                    d_rad = math.radians(float(mid_err))
                    c_d, s_d = math.cos(d_rad), math.sin(d_rad)
                    R_yaw = np.array([[c_d, 0.0, -s_d], [0.0, 1.0, 0.0], [s_d, 0.0, c_d]], dtype=np.float64)
                    rebuilt_cams.append(rebuilt_cams[-1] + step_vec @ R_yaw.T)
                for k_pos, s_i in enumerate(valid_i):
                    delta_c = rebuilt_cams[k_pos] - out_cams[s_i]
                    out_pts[s_i] += delta_c
                    out_cams[s_i] = rebuilt_cams[k_pos]
                meta["gyro_straightened_deg"] = round(float(np.max(np.abs(yaw_err))), 2)

        # 2. Step-count metric scale calibration (single-clip level)
        steps_seq = [float(imu_kf[i].get("steps_cum", 0.0)) for i in valid_i]
        d_steps = max(0.0, steps_seq[-1] - steps_seq[0])
        total_steps_all += d_steps
        if len(groups) == 1 and d_steps >= 3.0:
            est_walk_m = d_steps * 0.72
            path_len = float(
                sum(np.linalg.norm(out_cams[valid_i[k]] - out_cams[valid_i[k - 1]]) for k in range(1, len(valid_i)))
            )
            if path_len >= 0.45:
                s_adj = float(np.clip(est_walk_m / path_len, 0.68, 1.45))
                if abs(s_adj - 1.0) > 0.04:
                    piv = np.array([out_cams[valid_i[0], 0], 0.0, out_cams[valid_i[0], 2]], dtype=np.float64)
                    for i in idxs:
                        out_pts[i] = piv + s_adj * (out_pts[i] - piv)
                        out_cams[i] = piv + s_adj * (out_cams[i] - piv)
                    meta["step_scale_factor"] = round(s_adj, 3)

        # 3. Record median compass heading vs camera XZ forward
        if cam_fwds_gl is not None and len(cam_fwds_gl) == S:
            comp_vals = [float(imu_kf[i]["compass_yaw_deg"]) for i in valid_i if "compass_yaw_deg" in imu_kf[i]]
            if comp_vals:
                fw0 = np.asarray(cam_fwds_gl[valid_i[0]], dtype=np.float64)
                world_yaw0 = math.degrees(math.atan2(fw0[0], -fw0[2]))
                compass_offsets.append(round(((float(np.median(comp_vals)) - world_yaw0 + 180.0) % 360.0) - 180.0, 2))

    if compass_offsets:
        meta["compass_north_offset_deg"] = compass_offsets[0]
    if total_steps_all > 0:
        meta["imu_steps"] = round(total_steps_all, 1)
    return out_pts, out_cams, meta


def realign_clips_icp(
    wpts: np.ndarray,
    cols: np.ndarray,
    masks: np.ndarray,
    sem: np.ndarray,
    cams: np.ndarray,
    clip_ids_per_frame: Optional[List[str]],
    imu_kf: Optional[List[Optional[Dict[str, Any]]]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    When multiple video clips are spliced together, anchors on the clip spanning the
    largest 3D spatial footprint and aligns each coherent trajectory sub-chunk of each
    secondary clip via Gravity-Locked 4-DOF Sim(3) (scale, yaw, du, dv) maximizing
    same-class semantic overlap (OBJECTS + WALLS), across-corridor wall coplanarity
    and Manhattan sharpness, and camera height consistency.
    """
    if not clip_ids_per_frame or len(clip_ids_per_frame) != len(wpts):
        return wpts, cams
    uniq_clips = sorted(set(clip_ids_per_frame))
    if len(uniq_clips) < 2:
        return wpts, cams

    def clip_span(cid: str) -> float:
        idxs = [i for i, c in enumerate(clip_ids_per_frame) if c == cid]
        pts_l = [wpts[i][masks[i] & (sem[i] != SEM_IGNORE)] for i in idxs if np.any(masks[i])]
        if not pts_l:
            return 0.0
        pa = np.vstack(pts_l)
        return float(np.linalg.norm(np.percentile(pa[:, [0, 2]], 95, axis=0) - np.percentile(pa[:, [0, 2]], 5, axis=0)))

    ref_cid = max(uniq_clips, key=clip_span)
    ref_idxs = [i for i, c in enumerate(clip_ids_per_frame) if c == ref_cid]
    ref_cam_y = float(np.median(cams[ref_idxs, 1]))

    def _vox(pts: np.ndarray, vs: float = 0.05) -> np.ndarray:
        if len(pts) == 0:
            return np.zeros((0, 3), dtype=np.float64)
        p = o3d.geometry.PointCloud()
        p.points = o3d.utility.Vector3dVector(pts)
        return np.asarray(p.voxel_down_sample(vs).points)

    def gather_cls(src_pts: np.ndarray, idxs: List[int], cls_id: int, ymin: float) -> np.ndarray:
        pl = [src_pts[i][masks[i] & (sem[i] == cls_id) & (src_pts[i, :, :, 1] > ymin)] for i in idxs if np.any(masks[i] & (sem[i] == cls_id))]
        return np.vstack(pl) if pl else np.zeros((0, 3), dtype=np.float64)

    ref_walls = gather_cls(wpts, ref_idxs, SEM_WALL, 0.25)
    ref_objs = gather_cls(wpts, ref_idxs, SEM_OBJECT, 0.12)
    if len(ref_walls) < 100:
        return wpts, cams

    # Reference clip dominant wall axis and across-corridor (narrower) dimension
    axis_deg = _dominant_wall_axis_deg(ref_walls[:, [0, 2]])
    th_ax = math.radians(axis_deg)
    ca_ax, sa_ax = math.cos(th_ax), math.sin(th_ax)
    R_dom = np.array([[ca_ax, sa_ax], [-sa_ax, ca_ax]], dtype=np.float64)

    w_ref_vx = _vox(ref_walls, 0.05)
    o_ref_vx = _vox(ref_objs, 0.05)
    w_ref_uv = w_ref_vx[:, [0, 2]] @ R_dom.T
    span_u = float(np.percentile(w_ref_uv[:, 0], 95) - np.percentile(w_ref_uv[:, 0], 5))
    span_v = float(np.percentile(w_ref_uv[:, 1], 95) - np.percentile(w_ref_uv[:, 1], 5))
    across_col = 1 if span_v < span_u else 0
    long_col = 1 - across_col
    ref_cam_ac = float(np.median((cams[ref_idxs][:, [0, 2]] @ R_dom.T)[:, across_col]))

    def _gather_side_lr(pts_arr: np.ndarray, cam_arr: np.ndarray, idxs: List[int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        wl_l, wr_l, ob_l = [], [], []
        for i in idxs:
            mw = masks[i] & (sem[i] == SEM_WALL) & (pts_arr[i, :, :, 1] > 0.28)
            if np.any(mw):
                pw = pts_arr[i][mw]
                c_ac = float((cam_arr[i, [0, 2]] @ R_dom.T)[across_col])
                p_ac = (pw[:, [0, 2]] @ R_dom.T)[:, across_col]
                if np.any(p_ac < c_ac - 0.15):
                    wl_l.append(pw[p_ac < c_ac - 0.15])
                if np.any(p_ac > c_ac + 0.15):
                    wr_l.append(pw[p_ac > c_ac + 0.15])
            mo = masks[i] & (sem[i] == SEM_OBJECT) & (pts_arr[i, :, :, 1] > 0.12)
            if np.any(mo):
                ob_l.append(pts_arr[i][mo])

        def _filter_longitudinal_side(arr: np.ndarray) -> np.ndarray:
            if len(arr) < 15:
                return np.zeros((0, 3), dtype=np.float64)
            p = o3d.geometry.PointCloud()
            p.points = o3d.utility.Vector3dVector(arr)
            p = p.voxel_down_sample(0.06)
            if len(p.points) < 12:
                return np.asarray(p.points, dtype=np.float64)
            p.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.22, max_nn=20))
            pts_vx = np.asarray(p.points, dtype=np.float64)
            nrm = np.asarray(p.normals, dtype=np.float64)
            nuv = nrm[:, [0, 2]] @ R_dom.T
            # Keep walls parallel to corridor axis (exclude end-walls blocking the end!)
            keep = (np.abs(nrm[:, 1]) < 0.45) & (np.abs(nuv[:, across_col]) >= 0.65)
            return pts_vx[keep] if np.count_nonzero(keep) >= 8 else pts_vx

        return (
            _filter_longitudinal_side(np.vstack(wl_l) if wl_l else np.zeros((0, 3))),
            _filter_longitudinal_side(np.vstack(wr_l) if wr_l else np.zeros((0, 3))),
            _vox(np.vstack(ob_l), 0.06) if ob_l else np.zeros((0, 3)),
        )

    anc_l, anc_r, anchor_objs = _gather_side_lr(wpts, cams, ref_idxs)
    out_pts = wpts.copy()
    out_cams = cams.copy()
    _, H, W, _ = wpts.shape

    def _frame_fwd_xz(pts_arr: np.ndarray, cam_arr: np.ndarray, i: int) -> np.ndarray:
        fv = pts_arr[i, H // 2, W // 2] - cam_arr[i]
        f2 = np.array([float(fv[0]), float(fv[2])], dtype=np.float64)
        nm = float(np.hypot(f2[0], f2[1]))
        return f2 / max(1e-6, nm)

    for cid in uniq_clips:
        if cid == ref_cid:
            continue
        src_all_idxs = [i for i, c in enumerate(clip_ids_per_frame) if c == cid]
        if not src_all_idxs:
            continue

        # Segment clip at abrupt camera turns (>40 deg) or height/scale jumps (>8%)
        fwds_clip = {i: _frame_fwd_xz(out_pts, out_cams, i) for i in src_all_idxs}
        raw_chunks: List[List[int]] = [[src_all_idxs[0]]]
        for prev_i, cur_i in zip(src_all_idxs[:-1], src_all_idxs[1:]):
            ang = math.degrees(math.acos(float(np.clip(np.dot(fwds_clip[prev_i], fwds_clip[cur_i]), -1.0, 1.0))))
            h_rel = abs(float(out_cams[cur_i, 1] - out_cams[prev_i, 1])) / max(0.3, float(out_cams[prev_i, 1]))
            if ang > 40.0 or h_rel > 0.08:
                raw_chunks.append([cur_i])
            else:
                raw_chunks[-1].append(cur_i)

        # Merge tiny (<3 frame) sub-chunks with neighbor having closest heading direction
        chunks: List[List[int]] = [ch[:] for ch in raw_chunks]
        while len(chunks) >= 2 and any(len(c) < 3 for c in chunks):
            small_k = next(k for k, c in enumerate(chunks) if len(c) < 3)
            mf_s = np.mean([fwds_clip[i] for i in chunks[small_k]], axis=0)
            mf_s /= max(1e-6, float(np.linalg.norm(mf_s)))
            cands_m = []
            if small_k > 0:
                mf_l = np.mean([fwds_clip[i] for i in chunks[small_k - 1]], axis=0)
                mf_l /= max(1e-6, float(np.linalg.norm(mf_l)))
                cands_m.append((float(np.dot(mf_s, mf_l)), small_k - 1))
            if small_k + 1 < len(chunks):
                mf_r = np.mean([fwds_clip[i] for i in chunks[small_k + 1]], axis=0)
                mf_r /= max(1e-6, float(np.linalg.norm(mf_r)))
                cands_m.append((float(np.dot(mf_s, mf_r)), small_k + 1))
            target_k = max(cands_m, key=lambda x: x[0])[1]
            if target_k < small_k:
                chunks[target_k].extend(chunks[small_k])
            else:
                chunks[target_k] = chunks[small_k] + chunks[target_k]
            chunks.pop(small_k)

        for ch_idx, idxs in enumerate(chunks):
            sl_vx, sr_vx, o_src_vx = _gather_side_lr(out_pts, out_cams, idxs)
            if len(sl_vx) + len(sr_vx) < 30:
                continue

            src_cam_y = float(np.median(out_cams[idxs, 1]))
            s_height_hint = float(np.clip(ref_cam_y / max(0.25, src_cam_y), 0.90, 1.32))
            piv_xz = np.median(out_cams[idxs][:, [0, 2]], axis=0)
            piv = np.array([float(piv_xz[0]), 0.0, float(piv_xz[1])], dtype=np.float64)
            cam_ac_init = float((piv_xz @ R_dom.T)[across_col])

            rl_tree = cKDTree(anc_l) if len(anc_l) >= 10 else None
            rr_tree = cKDTree(anc_r) if len(anc_r) >= 10 else None
            o_anc_tree = cKDTree(anchor_objs) if len(anchor_objs) >= 10 else None

            all_ac_pts = [arr[:, [0, 2]] @ R_dom.T for arr in [anc_l, anc_r, sl_vx, sr_vx] if len(arr) > 0]
            ac_min = min(float(a[:, across_col].min()) for a in all_ac_pts) - 2.0
            ac_max = max(float(a[:, across_col].max()) for a in all_ac_pts) + 2.0
            bins_ac = np.arange(ac_min, ac_max, 0.08)

            hl_anc = np.minimum(1.0, np.histogram((anc_l[:, [0, 2]] @ R_dom.T)[:, across_col], bins=bins_ac)[0] / 5.0) if len(anc_l) > 0 else np.zeros(len(bins_ac) - 1)
            hr_anc = np.minimum(1.0, np.histogram((anc_r[:, [0, 2]] @ R_dom.T)[:, across_col], bins=bins_ac)[0] / 5.0) if len(anc_r) > 0 else np.zeros(len(bins_ac) - 1)

            def _score_4dof(sc_val: float, yaw_deg: float, d_long: float, d_ac: float, r_thresh: float) -> Tuple[float, np.ndarray]:
                th = math.radians(yaw_deg)
                c_y, s_y = math.cos(th), math.sin(th)
                R = np.array([[c_y, 0.0, s_y], [0.0, 1.0, 0.0], [-s_y, 0.0, c_y]], dtype=np.float64)
                # Convert (d_long, d_ac) corridor displacement into world (dx, dz)
                du, dv = (d_long, d_ac) if across_col == 1 else (d_ac, d_long)
                dx = du * ca_ax - dv * sa_ax
                dz = du * sa_ax + dv * ca_ax
                sh = np.array([dx, 0.0, dz], dtype=np.float64)

                l_cand = sc_val * ((sl_vx - piv) @ R.T) + piv + sh if len(sl_vx) > 0 else sl_vx
                r_cand = sc_val * ((sr_vx - piv) @ R.T) + piv + sh if len(sr_vx) > 0 else sr_vx
                hl_c = np.minimum(1.0, np.histogram((l_cand[:, [0, 2]] @ R_dom.T)[:, across_col], bins=bins_ac)[0] / 4.0) if len(l_cand) > 0 else 0.0
                hr_c = np.minimum(1.0, np.histogram((r_cand[:, [0, 2]] @ R_dom.T)[:, across_col], bins=bins_ac)[0] / 4.0) if len(r_cand) > 0 else 0.0
                lr_colin = 140.0 * (float(np.sum(hl_anc * hl_c)) + float(np.sum(hr_anc * hr_c)))

                w_frac = 0.0
                if rl_tree is not None and len(l_cand) > 0:
                    dl, _ = rl_tree.query(l_cand, k=1, distance_upper_bound=r_thresh)
                    w_frac += 350.0 * float(np.mean(dl < r_thresh))
                if rr_tree is not None and len(r_cand) > 0:
                    dr, _ = rr_tree.query(r_cand, k=1, distance_upper_bound=r_thresh)
                    w_frac += 350.0 * float(np.mean(dr < r_thresh))

                obj_sc = 0.0
                if o_anc_tree is not None and len(o_src_vx) > 0:
                    o_cand = sc_val * ((o_src_vx - piv) @ R.T) + piv + sh
                    do, _ = o_anc_tree.query(o_cand, k=1, distance_upper_bound=r_thresh)
                    obj_sc = 2.8 * float(np.sum(do < r_thresh))

                # Keep camera centerline inside hallway & avoid dragging non-overlapping far ends along hallway
                cam_ac_err = (cam_ac_init + d_ac) - ref_cam_ac
                reg_pen = 55.0 * (d_long * d_long) + 75.0 * (cam_ac_err * cam_ac_err) + 60.0 * ((sc_val - s_height_hint) ** 2) + 2.5 * (yaw_deg * yaw_deg)
                return obj_sc + lr_colin + w_frac - reg_pen, R

            # Optional compass-seeded yaw center when both clips have absolute glasses compass readings
            yaw_center = 0.0
            yaw_half_span = 9.0
            if imu_kf and len(imu_kf) == len(wpts):
                ref_c = [float(imu_kf[i]["compass_yaw_deg"]) for i in ref_idxs if imu_kf[i] and "compass_yaw_deg" in imu_kf[i]]
                ch_c = [float(imu_kf[i]["compass_yaw_deg"]) for i in idxs if imu_kf[i] and "compass_yaw_deg" in imu_kf[i]]
                if ref_c and ch_c:
                    dy_comp = ((float(np.median(ref_c)) - float(np.median(ch_c)) + 180.0) % 360.0) - 180.0
                    if abs(dy_comp) <= 45.0:
                        yaw_center = dy_comp * 0.5
                        yaw_half_span = 16.0

            sc_cands = sorted(set(np.round(np.linspace(min(1.0, s_height_hint), min(1.30, s_height_hint * 1.06), 6), 3)))
            best = None
            for sc_val in sc_cands:
                for yaw_deg in np.linspace(yaw_center - yaw_half_span, yaw_center + yaw_half_span, 21):
                    for d_long in np.linspace(-1.2, 1.2, 9):
                        for d_ac in np.linspace(-1.1, 1.1, 12):
                            tot, R_mat = _score_4dof(sc_val, float(yaw_deg), float(d_long), float(d_ac), r_thresh=0.28)
                            if best is None or tot > best[0]:
                                best = (tot, float(sc_val), float(yaw_deg), float(d_long), float(d_ac), R_mat)

            if best is not None:
                _, sb, yb, dlb, dab, _ = best
                best_fine = None
                for sc_val in [max(0.88, sb - 0.03), sb, min(1.32, sb + 0.03)]:
                    for yaw_deg in np.linspace(yb - 2.0, yb + 2.0, 9):
                        for d_long in np.linspace(dlb - 0.25, dlb + 0.25, 7):
                            for d_ac in np.linspace(dab - 0.20, dab + 0.20, 7):
                                tot, R_mat = _score_4dof(float(sc_val), float(yaw_deg), float(d_long), float(d_ac), r_thresh=0.14)
                                if best_fine is None or tot > best_fine[0]:
                                    best_fine = (tot, float(sc_val), float(yaw_deg), float(d_long), float(d_ac), R_mat)
                _, sb, yb, dlb, dab, Rb = best_fine
                du_b, dv_b = (dlb, dab) if across_col == 1 else (dab, dlb)
                dx_b = du_b * ca_ax - dv_b * sa_ax
                dz_b = du_b * sa_ax + dv_b * ca_ax
                sh_b = np.array([dx_b, 0.0, dz_b], dtype=np.float64)
                for i in idxs:
                    out_pts[i] = sb * ((out_pts[i] - piv) @ Rb.T) + piv + sh_b
                    out_cams[i] = sb * ((out_cams[i] - piv) @ Rb.T) + piv + sh_b
                al_ch, ar_ch, ao_ch = _gather_side_lr(out_pts, out_cams, idxs)
                if len(al_ch) > 0:
                    anc_l = np.vstack([anc_l, al_ch]) if len(anc_l) > 0 else al_ch
                if len(ar_ch) > 0:
                    anc_r = np.vstack([anc_r, ar_ch]) if len(anc_r) > 0 else ar_ch
                if len(ao_ch) > 0:
                    anchor_objs = np.vstack([anchor_objs, ao_ch]) if len(anchor_objs) > 0 else ao_ch
                print(
                    f"[SpatialModel] Semantic 4-DOF aligned '{cid[:15]}' chunk#{ch_idx} ({len(idxs)}f) -> '{ref_cid[:15]}': "
                    f"scale={sb:.3f} (hint={s_height_hint:.3f}), yaw={yb:+.2f}deg, d_long={dlb:+.2f}, d_across={dab:+.2f}m"
                )

    return out_pts, out_cams


def _point_in_poly_mask_2d(pts_xz: np.ndarray, poly_xz: np.ndarray, margin: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """Returns (inside_mask, min_edge_distance_m) for 2D polygon."""
    n = len(poly_xz)
    x, z = pts_xz[:, 0], pts_xz[:, 1]
    inside = np.zeros(len(pts_xz), dtype=bool)
    px, pz = poly_xz[:, 0], poly_xz[:, 1]
    j = n - 1
    for i in range(n):
        xi, zi = px[i], pz[i]
        xj, zj = px[j], pz[j]
        cond = ((zi > z) != (zj > z)) & (x < (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi)
        inside ^= cond
        j = i

    min_d = np.full(len(pts_xz), 1e9, dtype=np.float64)
    for i in range(n):
        a = poly_xz[i]
        b = poly_xz[(i + 1) % n]
        ab = b - a
        denom = float(np.dot(ab, ab))
        if denom < 1e-12:
            continue
        t = np.clip(((x - a[0]) * ab[0] + (z - a[1]) * ab[1]) / denom, 0.0, 1.0)
        dx = x - (a[0] + t * ab[0])
        dz = z - (a[1] + t * ab[1])
        d = np.hypot(dx, dz)
        min_d = np.minimum(min_d, d)

    if margin > 0:
        return inside | (min_d <= margin), min_d
    return inside, min_d


def _idw_sample_colors(
    query_pts: np.ndarray,
    ref_pts: np.ndarray,
    ref_cols: np.ndarray,
    fallback_col: np.ndarray,
    k: int = 8,
    max_dist: float = 1.5,
) -> np.ndarray:
    """Interpolates realistic local surface colors via k-NN inverse-distance weighting."""
    if len(query_pts) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    if len(ref_pts) < 4:
        return np.tile(fallback_col[None, :], (len(query_pts), 1))
    tree = cKDTree(ref_pts)
    kk = min(k, len(ref_pts))
    dists, idxs = tree.query(query_pts, k=kk, workers=-1)
    if kk == 1:
        dists = dists[:, None]
        idxs = idxs[:, None]
    out_cols = np.empty((len(query_pts), 3), dtype=np.float64)
    valid_row = np.min(dists, axis=1) <= max_dist
    if np.any(valid_row):
        w = 1.0 / np.maximum(dists[valid_row], 0.02) ** 2
        w_sum = np.sum(w, axis=1, keepdims=True)
        sampled = np.sum(ref_cols[idxs[valid_row]] * w[:, :, None], axis=1) / np.maximum(w_sum, 1e-9)
        # Blend gently with global surface tone for far extrapolations
        min_d = np.min(dists[valid_row], axis=1, keepdims=True)
        alpha_glob = np.clip((min_d - 0.15) / max(0.25, max_dist), 0.0, 0.55)
        out_cols[valid_row] = (1.0 - alpha_glob) * sampled + alpha_glob * fallback_col[None, :]
    if np.any(~valid_row):
        out_cols[~valid_row] = fallback_col[None, :]
    return np.clip(out_cols, 0.02, 0.98)


def solve_semantic_room_and_complete(
    wpts: np.ndarray,
    cols: np.ndarray,
    masks: np.ndarray,
    sem: np.ndarray,
    cams: np.ndarray,
    clip_ids_per_frame: Optional[List[str]] = None,
    max_raw_points: int = 200000,
    max_fill_points: int = 160000,
) -> Dict[str, Any]:
    """
    Full semantic 3D architecture solver, dot cleaner/snapping refiner, and surface completer.
    Returns ordered `[realigned_raw | ai_completed]` points + architectural `room_envelope`.
    """
    S, H, W = wpts.shape[:3]
    rng = np.random.default_rng(42)

    # Mask out camera-operator body/shoe pixels near bottom image border (<0.55m from cam)
    clean_masks = masks & (sem != SEM_IGNORE)
    row_grid = np.broadcast_to(np.arange(H, dtype=np.float64)[None, :, None], (S, H, W))
    dist_cam = np.linalg.norm(wpts - cams[:, None, None, :], axis=3)
    near_body = (row_grid >= H * 0.80) & (dist_cam < 0.62) & (sem != SEM_FLOOR)
    clean_masks &= ~near_body

    # Flatten valid points + semantic tags
    pts_all = wpts[clean_masks]
    cols_all = cols[clean_masks].astype(np.float64)
    sem_all = sem[clean_masks]

    # Drop severe specular below-floor ghosting before voxelization
    fl_pts_raw = pts_all[sem_all == SEM_FLOOR]
    fl_y0 = float(np.median(fl_pts_raw[:, 1])) if len(fl_pts_raw) >= 50 else 0.0
    pts_all[:, 1] -= fl_y0
    cams_adj = cams.copy()
    cams_adj[:, 1] -= fl_y0

    not_reflection = ~(pts_all[:, 1] < -0.045)
    pts_all = pts_all[not_reflection]
    cols_all = cols_all[not_reflection]
    sem_all = sem_all[not_reflection]

    # Downsample + statistical outlier pruning, then transfer semantic tag via exact nearest neighbor
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts_all)
    pcd.colors = o3d.utility.Vector3dVector(cols_all)
    pcd = pcd.voxel_down_sample(voxel_size=0.018)
    if len(pcd.points) >= 120:
        pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=24, std_ratio=1.35)
    if len(pcd.points) >= 120:
        pcd, _ = pcd.remove_radius_outlier(nb_points=6, radius=0.055)

    pts_vox = np.asarray(pcd.points, dtype=np.float64)
    cols_vox = np.asarray(pcd.colors, dtype=np.float64)
    _, nn_idx = cKDTree(pts_all).query(pts_vox, k=1, workers=-1)
    sem_vox = sem_all[nn_idx]

    # Estimate surface normals cleanly on structural subset
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.14, max_nn=28))
    nrm_vox = np.asarray(pcd.normals, dtype=np.float64)

    # Determine floor_y and true ceiling_y semantically!
    fl_m = sem_vox == SEM_FLOOR
    floor_y = round(float(np.median(pts_vox[fl_m, 1])) if np.count_nonzero(fl_m) >= 50 else 0.0, 3)

    wa_m = sem_vox == SEM_WALL
    ce_m = sem_vox == SEM_CEILING
    wall_top_est = float(np.percentile(pts_vox[wa_m, 1], 93)) if np.count_nonzero(wa_m) >= 100 else 2.4
    cam_top = float(np.percentile(cams_adj[:, 1], 90)) + 0.85 if len(cams_adj) > 0 else 2.4
    ce_flat_m = ce_m & (np.abs(nrm_vox[:, 1]) >= 0.55)
    ce_src_m = ce_flat_m if np.count_nonzero(ce_flat_m) >= 80 else ce_m
    if np.count_nonzero(ce_src_m) >= 80:
        ceil_pts_y = pts_vox[ce_src_m, 1]
        # Lock onto the densest structural ceiling slab peak (avoids high upward skylight/ray skew)
        y_lo = floor_y + 2.20
        y_hi = max(y_lo + 0.5, float(np.percentile(ceil_pts_y, 92)))
        c_band = ceil_pts_y[(ceil_pts_y >= y_lo - 0.1) & (ceil_pts_y <= y_hi + 0.1)]
        if len(c_band) >= 40:
            hc, ec = np.histogram(c_band, bins=np.arange(y_lo - 0.1, y_hi + 0.16, 0.05))
            win = hc[:-2] + hc[1:-1] + hc[2:] if len(hc) >= 3 else hc
            k_pk = int(np.argmax(win))
            peak_c = float(ec[k_pk + 1]) if len(hc) >= 3 else 0.5 * float(ec[k_pk] + ec[k_pk + 1])
            near_pk = c_band[np.abs(c_band - peak_c) <= 0.14]
            roof_obs = float(np.median(near_pk)) if len(near_pk) >= 20 else float(np.percentile(ceil_pts_y, 50))
        else:
            roof_obs = float(np.percentile(ceil_pts_y, 50))
        roof_y = round(float(max(roof_obs, cam_top, floor_y + 2.2)), 3)
    else:
        roof_y = round(float(max(wall_top_est + 0.12, cam_top, floor_y + 2.2)), 3)

    # Extract dominant room axis from spatial Manhattan wall sharpness (avoids close-wall bias)
    vert_wall_m = wa_m & (np.abs(nrm_vox[:, 1]) < 0.38) & (pts_vox[:, 1] > floor_y + 0.12)
    dom_deg = (
        _dominant_wall_axis_deg(pts_vox[vert_wall_m][:, [0, 2]])
        if np.count_nonzero(vert_wall_m) >= 80
        else 0.0
    )
    c_d, s_d = math.cos(math.radians(dom_deg)), math.sin(math.radians(dom_deg))
    R_to_dom = np.array([[c_d, s_d], [-s_d, c_d]], dtype=np.float64)

    pts_uv = pts_vox[:, [0, 2]] @ R_to_dom.T
    cams_uv = cams_adj[:, [0, 2]] @ R_to_dom.T

    # Use core structural points (WALL, FLOOR, CEILING, OBJECT — excluding see-through GLASS/DOOR extremes) for bounds
    core_pts_m = (sem_vox == SEM_FLOOR) | (sem_vox == SEM_WALL) | (sem_vox == SEM_CEILING) | (sem_vox == SEM_OBJECT)
    uv_ref = pts_uv[core_pts_m] if np.count_nonzero(core_pts_m) >= 200 else pts_uv
    umin = float(min(np.percentile(uv_ref[:, 0], 1), cams_uv[:, 0].min()))
    umax = float(max(np.percentile(uv_ref[:, 0], 99), cams_uv[:, 0].max()))
    vmin = float(min(np.percentile(uv_ref[:, 1], 1), cams_uv[:, 1].min()))
    vmax = float(max(np.percentile(uv_ref[:, 1], 99), cams_uv[:, 1].max()))
    span_uv = max(1.5, float(max(umax - umin, vmax - vmin)))

    # Fine 2D metric architectural grid (~4-6 cm cells even on 16m corridors)
    G = 320
    pad_m = 0.08 * span_uv
    cell = (span_uv + 2.0 * pad_m) / (G - 20)
    origin_u = umin - pad_m - 10.0 * cell
    origin_v = vmin - pad_m - 10.0 * cell

    iu = np.clip(((pts_uv[:, 0] - origin_u) / cell).astype(np.int32), 1, G - 2)
    iv = np.clip(((pts_uv[:, 1] - origin_v) / cell).astype(np.int32), 1, G - 2)

    wall_cnt = np.zeros((G, G), dtype=np.int32)
    door_cnt = np.zeros((G, G), dtype=np.int32)
    floor_cnt = np.zeros((G, G), dtype=np.int32)
    ceil_cnt = np.zeros((G, G), dtype=np.int32)
    obj_cnt = np.zeros((G, G), dtype=np.int32)
    max_wall_y = np.full((G, G), -1e9, dtype=np.float64)

    # Exclude foreground OBJECTS & low baseboards from slicing the floor!
    pure_wall_m = vert_wall_m & (pts_vox[:, 1] > floor_y + 0.22)
    np.add.at(wall_cnt, (iu[pure_wall_m], iv[pure_wall_m]), 1)
    np.maximum.at(max_wall_y, (iu[pure_wall_m], iv[pure_wall_m]), pts_vox[pure_wall_m, 1])
    door_m = (sem_vox == SEM_DOOR) & (np.abs(nrm_vox[:, 1]) < 0.45) & (pts_vox[:, 1] > floor_y + 0.15)
    np.add.at(door_cnt, (iu[door_m], iv[door_m]), 1)
    np.add.at(floor_cnt, (iu[fl_m], iv[fl_m]), 1)
    np.add.at(ceil_cnt, (iu[ce_m], iv[ce_m]), 1)
    obj_m = (sem_vox == SEM_OBJECT) & (pts_vox[:, 1] > floor_y + 0.08)
    np.add.at(obj_cnt, (iu[obj_m], iv[obj_m]), 1)

    wall_raw = ((wall_cnt >= 2) & (max_wall_y >= floor_y + 0.45)).astype(np.uint8)
    n_wl, wl_lbls, wl_stats, _ = cv2.connectedComponentsWithStats(wall_raw)
    wall_solid = np.zeros_like(wall_raw)
    wall_macro = np.zeros_like(wall_raw)
    min_macro_diag = max(6.0, 0.55 / cell)  # >= 55cm architectural wall
    for lid in range(1, n_wl):
        if wl_stats[lid, cv2.CC_STAT_AREA] >= 3:
            wall_solid[wl_lbls == lid] = 1
            if math.hypot(wl_stats[lid, cv2.CC_STAT_WIDTH], wl_stats[lid, cv2.CC_STAT_HEIGHT]) >= min_macro_diag:
                wall_macro[wl_lbls == lid] = 1
    wall_barrier = cv2.morphologyEx(wall_macro, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    seen_int = ((floor_cnt >= 1) & (wall_barrier == 0)).astype(np.uint8)
    env_support = ((floor_cnt >= 1) | (ceil_cnt >= 1) | (wall_cnt >= 1) | (obj_cnt >= 1) | (door_cnt >= 1)).astype(np.uint8)
    cam_cells = np.column_stack([
        np.clip(((cams_uv[:, 0] - origin_u) / cell).astype(np.int32), 2, G - 3),
        np.clip(((cams_uv[:, 1] - origin_v) / cell).astype(np.int32), 2, G - 3),
    ])
    cam_path = np.zeros((G, G), dtype=np.uint8)
    cam_r = max(3, int(round(0.22 / cell)))
    for i in range(len(cam_cells)):
        c0 = (int(cam_cells[i, 1]), int(cam_cells[i, 0]))
        cv2.circle(seen_int, c0, cam_r, 1, -1)
        cv2.circle(env_support, c0, cam_r, 1, -1)
        cv2.circle(cam_path, c0, cam_r, 1, -1)
        cv2.circle(wall_barrier, c0, 2, 0, -1)
        if i > 0:
            c1 = (int(cam_cells[i - 1, 1]), int(cam_cells[i - 1, 0]))
            cv2.line(seen_int, c1, c0, 1, thickness=max(3, cam_r))
            cv2.line(env_support, c1, c0, 1, thickness=max(3, cam_r))
            cv2.line(cam_path, c1, c0, 1, thickness=max(3, cam_r))
            cv2.line(wall_barrier, c1, c0, 0, thickness=2)

    seen_int = cv2.morphologyEx(seen_int, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    env_close = cv2.morphologyEx(env_support, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    env_reach = cv2.dilate(env_close, np.ones((15, 15), np.uint8))

    adj_walls = wall_barrier & cv2.dilate(seen_int, np.ones((19, 19), np.uint8))
    core_mask = (seen_int > 0) | (cam_path > 0) | (adj_walls > 0)
    cu, cv_arr = np.where(core_mask)
    if len(cu) < 4:
        return {"points": [], "keyframes": [], "wall_segments": [], "predicted_openings": [], "room_envelope": {}, "raw_point_count": 0, "completed_point_count": 0}

    u0b, u1b = max(2, int(cu.min()) - 2), min(G - 3, int(cu.max()) + 2)
    v0b, v1b = max(2, int(cv_arr.min()) - 2), min(G - 3, int(cv_arr.max()) + 2)
    gu, gv = np.meshgrid(np.arange(u0b, u1b + 1, dtype=np.float64), np.arange(v0b, v1b + 1, dtype=np.float64), indexing="ij")
    box_cells = np.column_stack([gu.ravel(), gv.ravel()])

    # Multi-view ray carve: keeps visible interior bounded by observed support or real backing wall
    k_idx = np.linspace(0, len(cam_cells) - 1, min(len(cam_cells), 18), dtype=int)
    ray_cams = cam_cells[k_idx].astype(np.float64)
    ts = np.linspace(0.06, 0.90, 22, dtype=np.float64)[None, None, :]
    ru = np.clip(np.rint(ray_cams[None, :, 0:1] + ts * (box_cells[:, None, 0:1] - ray_cams[None, :, 0:1])).astype(np.int32), 0, G - 1)
    rv = np.clip(np.rint(ray_cams[None, :, 1:2] + ts * (box_cells[:, None, 1:2] - ray_cams[None, :, 1:2])).astype(np.int32), 0, G - 1)
    unblocked_ray = np.any(~np.any(wall_barrier[ru, rv] > 0, axis=2), axis=1)

    t_bk = np.linspace(1.02, 1.55, 8, dtype=np.float64)[None, None, :]
    bu = np.clip(np.rint(ray_cams[None, :, 0:1] + t_bk * (box_cells[:, None, 0:1] - ray_cams[None, :, 0:1])).astype(np.int32), 0, G - 1)
    bv = np.clip(np.rint(ray_cams[None, :, 1:2] + t_bk * (box_cells[:, None, 1:2] - ray_cams[None, :, 1:2])).astype(np.int32), 0, G - 1)
    backed_by_wall = np.any((~np.any(wall_barrier[ru, rv] > 0, axis=2)) & np.any(wall_barrier[bu, bv] > 0, axis=2), axis=1)

    bx_i = box_cells[:, 0].astype(np.int32)
    by_i = box_cells[:, 1].astype(np.int32)
    visible_any = unblocked_ray & ((env_reach[bx_i, by_i] > 0) | backed_by_wall)

    room_mask = np.zeros((G, G), dtype=np.uint8)
    room_mask[bx_i[visible_any], by_i[visible_any]] = 255
    room_mask[cam_path > 0] = 255
    room_mask[(seen_int > 0) & (wall_barrier == 0)] = 255
    room_mask = cv2.morphologyEx(room_mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    n_rm, rm_lbls, rm_stats, _ = cv2.connectedComponentsWithStats(room_mask)
    best_rm = 1 + int(np.argmax(rm_stats[1:, cv2.CC_STAT_AREA]))
    clean_room = (rm_lbls == best_rm).astype(np.uint8) * 255

    # Remove interior cavities caused by furniture/cart/floor shadows; keep ONLY true tall >= 1.45m structural columns!
    inv_r = 255 - clean_room
    n_h, h_lbls, h_stats, _ = cv2.connectedComponentsWithStats(inv_r)
    col_wall_m = (wall_macro > 0) & (max_wall_y >= floor_y + 1.45)
    for hid in range(1, n_h):
        if hid == h_lbls[0, 0]:
            continue
        area_px = int(h_stats[hid, cv2.CC_STAT_AREA])
        hole_m = h_lbls == hid
        n_tall_wall = int(np.count_nonzero(col_wall_m & hole_m))
        if area_px * (cell * cell) < 0.22 or n_tall_wall < max(5, int(0.16 * area_px)):
            clean_room[hole_m] = 255

    wall_zone = cv2.dilate(wall_solid | wall_barrier, np.ones((15, 15), np.uint8))
    door_zone = cv2.dilate((door_cnt >= 2).astype(np.uint8), np.ones((11, 11), np.uint8))
    seen_zone = cv2.dilate(seen_int | cam_path, np.ones((9, 9), np.uint8))

    w_uv_ref = pts_uv[pure_wall_m]
    w_nuv_ref = nrm_vox[pure_wall_m][:, [0, 2]] @ R_to_dom.T

    def _snap_poly_uv_to_scanned_walls(poly_uv_in: np.ndarray) -> np.ndarray:
        m_v = len(poly_uv_in)
        if m_v < 3 or len(w_uv_ref) < 25:
            return poly_uv_in
        norms = np.zeros((m_v, 2), dtype=np.float64)
        c_new = np.zeros(m_v, dtype=np.float64)
        shifts = np.zeros(m_v, dtype=np.float64)
        orig_vecs = np.zeros((m_v, 2), dtype=np.float64)
        orig_lens = np.zeros(m_v, dtype=np.float64)

        for i in range(m_v):
            a = poly_uv_in[i]
            b = poly_uv_in[(i + 1) % m_v]
            du, dv = float(b[0] - a[0]), float(b[1] - a[1])
            slen = math.hypot(du, dv)
            orig_vecs[i] = [du, dv]
            orig_lens[i] = slen
            if slen < 1e-6:
                norms[i] = [0.0, 1.0]
                c_new[i] = float(a[1])
                continue
            tu, tv = du / slen, dv / slen
            nu, nv = -tv, tu
            norms[i] = [nu, nv]
            c_orig = nu * float(a[0]) + nv * float(a[1])
            c_new[i] = c_orig

            n_s = max(6, int(slen / max(0.04, cell * 1.5)))
            hw = 0
            for tt in np.linspace(0.08, 0.92, n_s):
                suv = (1.0 - tt) * a + tt * b
                si = int(np.clip((suv[0] - origin_u) / cell, 0, G - 1))
                sj = int(np.clip((suv[1] - origin_v) / cell, 0, G - 1))
                if wall_zone[si, sj] > 0:
                    hw += 1
            if hw < max(2, int(0.24 * n_s)) or slen < 0.35:
                continue

            vx = w_uv_ref[:, 0] - float(a[0])
            vy = w_uv_ref[:, 1] - float(a[1])
            along = vx * tu + vy * tv
            perp = vx * nu + vy * nv
            ndot = np.abs(w_nuv_ref[:, 0] * nu + w_nuv_ref[:, 1] * nv)
            pad = min(0.12, 0.15 * slen)
            in_m = (along >= pad) & (along <= slen - pad) & (np.abs(perp) <= 0.42) & (ndot >= 0.70)
            if np.count_nonzero(in_m) < 25:
                continue
            p_in = perp[in_m]
            a_in = along[in_m]
            hist_p, edges_p = np.histogram(p_in, bins=28, range=(-0.42, 0.42))
            b_best = int(np.argmax(hist_p))
            p_peak = 0.5 * float(edges_p[b_best] + edges_p[b_best + 1])
            near_m = np.abs(p_in - p_peak) <= 0.08
            if np.count_nonzero(near_m) >= 18 and float(np.ptp(a_in[near_m])) >= min(0.25, 0.30 * slen):
                d_shift = float(np.median(p_in[near_m]))
                c_new[i] = c_orig + d_shift
                shifts[i] = d_shift

        out_uv = np.zeros_like(poly_uv_in, dtype=np.float64)
        for i in range(m_v):
            ep = (i - 1) % m_v
            ec = i
            det = norms[ep, 0] * norms[ec, 1] - norms[ep, 1] * norms[ec, 0]
            if abs(det) >= 0.25:
                px = (c_new[ep] * norms[ec, 1] - norms[ep, 1] * c_new[ec]) / det
                py = (norms[ep, 0] * c_new[ec] - c_new[ep] * norms[ec, 0]) / det
                out_uv[i] = [px, py]
            else:
                n_avg = 0.5 * (norms[ep] + norms[ec])
                n_norm = float(np.hypot(n_avg[0], n_avg[1]))
                if n_norm > 1e-6:
                    n_avg /= n_norm
                out_uv[i] = poly_uv_in[i] + 0.5 * (shifts[ep] + shifts[ec]) * n_avg

        def _signed_area(p: np.ndarray) -> float:
            q = np.roll(p, -1, axis=0)
            return 0.5 * float(np.sum(p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]))

        area_old = _signed_area(poly_uv_in)
        area_new = _signed_area(out_uv)
        if abs(area_old) < 1e-6 or area_new * area_old <= 0 or abs(area_new) < 0.50 * abs(area_old):
            return poly_uv_in
        for i in range(m_v):
            if orig_lens[i] >= 0.25:
                nv_vec = out_uv[(i + 1) % m_v] - out_uv[i]
                if float(nv_vec[0] * orig_vecs[i, 0] + nv_vec[1] * orig_vecs[i, 1]) <= 0.0:
                    return poly_uv_in
        return out_uv

    contours, hierarchy = cv2.findContours(clean_room, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    hier = hierarchy[0]
    outer_idxs = [i for i in range(len(contours)) if hier[i][3] == -1]
    main_idx = max(outer_idxs, key=lambda idx: cv2.contourArea(contours[idx]))

    wall_segments: List[List[float]] = []
    predicted_openings: List[List[float]] = []
    room_polygon_world: List[List[float]] = []

    for c_idx in range(len(contours)):
        is_outer = c_idx == main_idx
        is_hole = (hier[c_idx][3] == main_idx) and (cv2.contourArea(contours[c_idx]) * (cell * cell) >= 0.16)
        if not (is_outer or is_hole):
            continue
        cnt = contours[c_idx]
        if len(cnt) < 3:
            continue
        pts_uv_cnt = np.column_stack([
            origin_u + (cnt[:, 0, 1] + 0.5) * cell,
            origin_v + (cnt[:, 0, 0] + 0.5) * cell,
        ]).astype(np.float32).reshape(-1, 1, 2)
        eps_poly = min(0.16, max(0.08, 0.014 * span_uv))
        simp_uv = cv2.approxPolyDP(pts_uv_cnt, epsilon=eps_poly, closed=True).reshape(-1, 2)
        snapped_uv = _snap_aligned_polygon(simp_uv, span_uv=span_uv)
        snapped_uv = _snap_poly_uv_to_scanned_walls(snapped_uv)
        poly_world = snapped_uv @ R_to_dom

        if is_outer:
            room_polygon_world = [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in poly_world]

        m_v = len(poly_world)
        for i in range(m_v):
            p1, p2 = poly_world[i], poly_world[(i + 1) % m_v]
            seg_len = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
            if seg_len < 0.08:
                continue
            n_samp = max(6, int(seg_len / max(0.04, cell * 1.5)))
            ts_e = np.linspace(0.08, 0.92, n_samp)
            hit_wall, hit_seen, hit_door = 0, 0, 0
            for tt in ts_e:
                suv = (1.0 - tt) * snapped_uv[i] + tt * snapped_uv[(i + 1) % m_v]
                si = int(np.clip((suv[0] - origin_u) / cell, 0, G - 1))
                sj = int(np.clip((suv[1] - origin_v) / cell, 0, G - 1))
                if wall_zone[si, sj] > 0:
                    hit_wall += 1
                elif door_zone[si, sj] > 0:
                    hit_door += 1
                elif seen_zone[si, sj] > 0:
                    hit_seen += 1
            obs_wall = 1.0 if hit_wall >= max(2, int(0.24 * n_samp)) else 0.0
            wall_segments.append([
                round(float(p1[0]), 4), round(float(p1[1]), 4),
                round(float(p2[0]), 4), round(float(p2[1]), 4),
                floor_y, roof_y, obs_wall,
            ])
            if is_outer and ((obs_wall == 0.0 and hit_seen >= max(2, int(0.45 * n_samp))) or hit_door >= max(2, int(0.30 * n_samp))):
                predicted_openings.append([
                    round(float(p1[0]), 4), round(float(p1[1]), 4),
                    round(float(p2[0]), 4), round(float(p2[1]), 4),
                    floor_y, round(floor_y + 0.80 * (roof_y - floor_y), 3),
                ])

    poly_np = np.asarray(room_polygon_world, dtype=np.float64) if len(room_polygon_world) >= 3 else None

    # Cull see-through GLASS / behind-wall noise strictly outside the wall-snapped room polygon
    # and noisy upward ceiling ray overshoot > 0.14m above the solved architectural roof
    if poly_np is not None:
        in_room, edge_dist = _point_in_poly_mask_2d(pts_vox[:, [0, 2]], poly_np, margin=0.07)
        glass_outside = ((sem_vox == SEM_GLASS) | (sem_vox == SEM_DOOR)) & (~in_room) & (edge_dist > 0.09)
        far_outside = (~in_room) & (edge_dist > 0.16)
        above_roof = pts_vox[:, 1] > (roof_y + 0.14)
        keep_interior = ~(glass_outside | far_outside | above_roof)
        pts_vox = pts_vox[keep_interior]
        cols_vox = cols_vox[keep_interior]
        sem_vox = sem_vox[keep_interior]
        nrm_vox = nrm_vox[keep_interior]
    else:
        keep_y = pts_vox[:, 1] <= (roof_y + 0.14)
        pts_vox = pts_vox[keep_y]
        cols_vox = cols_vox[keep_y]
        sem_vox = sem_vox[keep_y]
        nrm_vox = nrm_vox[keep_y]

    # Semantic Dot Realignment / Snapping on surviving scanned dots:
    # 1) Snap ALL semantic FLOOR points firmly onto floor_y -> zero double layer!
    fl_snap = (sem_vox == SEM_FLOOR) & (np.abs(pts_vox[:, 1] - floor_y) <= 0.15)
    pts_vox[fl_snap, 1] += 0.92 * (floor_y - pts_vox[fl_snap, 1])

    # 2) Snap structural ceiling deck dots softly onto roof_y while preserving hanging ducts below roof
    ce_snap = (sem_vox == SEM_CEILING) & (np.abs(nrm_vox[:, 1]) >= 0.72) & (np.abs(pts_vox[:, 1] - roof_y) <= 0.16)
    pts_vox[ce_snap, 1] += 0.75 * (roof_y - pts_vox[ce_snap, 1])

    # 3) Pull semantic WALL dots softly toward their nearest crisp straight wall segment plane
    wa_idx = np.where((sem_vox == SEM_WALL) & (np.abs(nrm_vox[:, 1]) < 0.42))[0]
    if len(wa_idx) > 0 and wall_segments:
        wa_xz = pts_vox[wa_idx][:, [0, 2]]
        wa_n = nrm_vox[wa_idx][:, [0, 2]]
        for x1, z1, x2, z2, _, _, _ in wall_segments:
            dx, dz = x2 - x1, z2 - z1
            slen = math.hypot(dx, dz)
            if slen < 0.15:
                continue
            tx, tz = dx / slen, dz / slen
            nx, nz = -tz, tx
            vx, vz = wa_xz[:, 0] - x1, wa_xz[:, 1] - z1
            along = vx * tx + vz * tz
            perp = vx * nx + vz * nz
            ndot = np.abs(wa_n[:, 0] * nx + wa_n[:, 1] * nz)
            on_seg = (along >= -0.02) & (along <= slen + 0.02) & (np.abs(perp) <= 0.12) & (ndot >= 0.78)
            if np.any(on_seg):
                sel_i = wa_idx[on_seg]
                pts_vox[sel_i, 0] -= 0.78 * perp[on_seg] * nx
                pts_vox[sel_i, 2] -= 0.78 * perp[on_seg] * nz

    # Downsample scanned realigned cloud if over budget
    if len(pts_vox) > max_raw_points:
        sel = np.linspace(0, len(pts_vox) - 1, max_raw_points, dtype=int)
        pts_vox = pts_vox[sel]
        cols_vox = cols_vox[sel]
        sem_vox = sem_vox[sel]

    # Sample mean surface appearance colors for walls, floor, ceiling
    floor_fallback = np.median(cols_vox[sem_vox == SEM_FLOOR], axis=0) if np.any(sem_vox == SEM_FLOOR) else np.array([0.65, 0.65, 0.65])
    wall_fallback = np.median(cols_vox[sem_vox == SEM_WALL], axis=0) if np.any(sem_vox == SEM_WALL) else np.array([0.74, 0.74, 0.72])
    ceil_fallback = np.median(cols_vox[sem_vox == SEM_CEILING], axis=0) if np.any(sem_vox == SEM_CEILING) else np.array([0.72, 0.72, 0.70])

    # =====================================================================
    # AI SURFACE COMPLETION: Fill missing Floor, Walls & Ceiling seamlessly!
    # =====================================================================
    fill_pts_list: List[np.ndarray] = []
    fill_cols_list: List[np.ndarray] = []
    wall_colors: List[List[float]] = []

    if poly_np is not None:
        p_min_x, p_min_z = poly_np.min(axis=0)
        p_max_x, p_max_z = poly_np.max(axis=0)
        fl_step = 0.032
        xs_g = np.arange(p_min_x, p_max_x + 0.5 * fl_step, fl_step)
        zs_g = np.arange(p_min_z, p_max_z + 0.5 * fl_step, fl_step)
        gx, gz = np.meshgrid(xs_g, zs_g, indexing="xy")
        cand_xz = np.column_stack([gx.ravel(), gz.ravel()])
        # Add subtle 3mm organic jitter so completed surfaces match physical points naturally
        cand_xz += rng.uniform(-0.004, 0.004, size=cand_xz.shape)
        in_p, _ = _point_in_poly_mask_2d(cand_xz, poly_np, margin=0.0)
        cand_xz = cand_xz[in_p]

        # 1. Floor Completion where floor/object scan has holes
        if len(cand_xz) > 0:
            low_pts = pts_vox[(sem_vox == SEM_FLOOR) | (pts_vox[:, 1] <= floor_y + 0.25)]
            if len(low_pts) >= 10:
                dists_fl, _ = cKDTree(low_pts[:, [0, 2]]).query(cand_xz, k=1, workers=-1)
                miss_fl = cand_xz[dists_fl > 0.038]
            else:
                miss_fl = cand_xz
            if len(miss_fl) > 0:
                fl_q = np.column_stack([miss_fl[:, 0], np.full(len(miss_fl), floor_y, dtype=np.float64) + rng.uniform(-0.002, 0.002, len(miss_fl)), miss_fl[:, 1]])
                fl_ref_m = sem_vox == SEM_FLOOR
                fl_c = _idw_sample_colors(fl_q, pts_vox[fl_ref_m], cols_vox[fl_ref_m], floor_fallback, max_dist=1.8)
                fill_pts_list.append(fl_q)
                fill_cols_list.append(fl_c)

        # 2. Ceiling Completion across unobserved upper ceiling plane (dedicated uniform 4.5cm grid)
        ce_step = 0.045
        xs_c = np.arange(p_min_x, p_max_x + 0.5 * ce_step, ce_step)
        zs_c = np.arange(p_min_z, p_max_z + 0.5 * ce_step, ce_step)
        cgx, cgz = np.meshgrid(xs_c, zs_c, indexing="xy")
        ce_cand_xz = np.column_stack([cgx.ravel(), cgz.ravel()])
        ce_cand_xz += rng.uniform(-0.004, 0.004, size=ce_cand_xz.shape)
        in_pc, _ = _point_in_poly_mask_2d(ce_cand_xz, poly_np, margin=0.0)
        ce_cand_xz = ce_cand_xz[in_pc]
        if len(ce_cand_xz) > 0:
            ce_ref_m = (sem_vox == SEM_CEILING) | (pts_vox[:, 1] >= roof_y - 0.35)
            ce_cand_3d = np.column_stack([ce_cand_xz[:, 0], np.full(len(ce_cand_xz), roof_y, dtype=np.float64) + rng.uniform(-0.003, 0.003, len(ce_cand_xz)), ce_cand_xz[:, 1]])
            if np.count_nonzero(ce_ref_m) >= 10:
                d_ce, _ = cKDTree(pts_vox[ce_ref_m]).query(ce_cand_3d, k=1, workers=-1)
                miss_ce = ce_cand_3d[d_ce > 0.055]
            else:
                miss_ce = ce_cand_3d
            if len(miss_ce) > 0:
                c_sem_m = sem_vox == SEM_CEILING
                ce_c = _idw_sample_colors(miss_ce, pts_vox[c_sem_m], cols_vox[c_sem_m], ceil_fallback, max_dist=3.0)
                fill_pts_list.append(miss_ce)
                fill_cols_list.append(ce_c)

    # 3. Wall Completion from floor_y -> roof_y (skipping only actual open doorway corridors)
    wa_ref_m = (sem_vox == SEM_WALL)
    wa_tree = cKDTree(pts_vox[wa_ref_m]) if np.count_nonzero(wa_ref_m) >= 10 else None
    w_step_s, w_step_y = 0.038, 0.038
    open_set = {(o[0], o[1], o[2], o[3]) for o in predicted_openings}

    for x1, z1, x2, z2, y0, y1, obs_w in wall_segments:
        dx, dz = x2 - x1, z2 - z1
        slen = math.hypot(dx, dz)
        if slen < 0.08:
            wall_colors.append([round(float(x), 3) for x in wall_fallback])
            continue
        is_door_edge = any(abs(ox1 - x1) < 0.05 and abs(oz1 - z1) < 0.05 and abs(ox2 - x2) < 0.05 and abs(oz2 - z2) < 0.05 for ox1, oz1, ox2, oz2 in open_set)
        # Skip low doorway aperture only on open exit corridors; close above door lintel up to roof
        ys_min = y0 + (0.78 * (y1 - y0) if (is_door_edge and obs_w == 0.0) else 0.0)
        ss = np.arange(0.02, slen - 0.01, w_step_s)
        ys = np.arange(ys_min, y1, w_step_y)
        if len(ss) == 0 or len(ys) == 0:
            wall_colors.append([round(float(x), 3) for x in wall_fallback])
            continue
        gs, gy = np.meshgrid(ss, ys, indexing="xy")
        s_flat = np.clip(gs.ravel() + rng.uniform(-0.012, 0.012, len(gs.ravel())), 0.01, slen - 0.01)
        t_flat = s_flat / slen
        y_flat = np.clip(gy.ravel() + rng.uniform(-0.012, 0.012, len(gs.ravel())), ys_min + 0.004, y1 - 0.004)
        wx = x1 + t_flat * dx
        wz = z1 + t_flat * dz
        cand_w = np.column_stack([wx, y_flat, wz])

        # Record representative observed wall color
        if wa_tree is not None:
            dw, _ = wa_tree.query(cand_w, k=1, workers=-1)
            obs_near = cand_w[dw <= 0.08]
            miss_w = cand_w[dw > 0.048]
        else:
            obs_near = np.zeros((0, 3))
            miss_w = cand_w

        if len(obs_near) >= 8 and np.any(wa_ref_m):
            local_w_col = np.median(_idw_sample_colors(obs_near[::max(1, len(obs_near)//64)], pts_vox[wa_ref_m], cols_vox[wa_ref_m], wall_fallback, max_dist=1.2), axis=0)
        else:
            local_w_col = wall_fallback
        wall_colors.append([round(float(c), 3) for c in local_w_col])

        if len(miss_w) > 0:
            w_c = _idw_sample_colors(miss_w, pts_vox[wa_ref_m], cols_vox[wa_ref_m], local_w_col, max_dist=1.4)
            fill_pts_list.append(miss_w)
            fill_cols_list.append(w_c)

    if fill_pts_list:
        fill_pts = np.vstack(fill_pts_list)
        fill_cols = np.vstack(fill_cols_list)
        if len(fill_pts) > max_fill_points:
            # Uniform spatial voxel-like stride across regular grids (never random confetti holes)
            sel_f = np.linspace(0, len(fill_pts) - 1, max_fill_points, dtype=int)
            fill_pts = fill_pts[sel_f]
            fill_cols = fill_cols[sel_f]
    else:
        fill_pts = np.zeros((0, 3), dtype=np.float64)
        fill_cols = np.zeros((0, 3), dtype=np.float64)

    raw_cnt = len(pts_vox)
    fill_cnt = len(fill_pts)
    combined_pts = np.vstack([pts_vox, fill_pts]) if fill_cnt > 0 else pts_vox
    combined_cols = np.vstack([cols_vox, fill_cols]) if fill_cnt > 0 else cols_vox

    points_list = [
        [
            round(float(combined_pts[i, 0]), 4),
            round(float(combined_pts[i, 1]), 4),
            round(float(combined_pts[i, 2]), 4),
            round(float(combined_cols[i, 0]), 3),
            round(float(combined_cols[i, 1]), 3),
            round(float(combined_cols[i, 2]), 3),
        ]
        for i in range(len(combined_pts))
    ]

    kfs_out = []
    for s_i in range(S):
        p_cam = cams_adj[s_i]
        entry: Dict[str, Any] = {
            "index": s_i + 1,
            "pose": [round(float(p_cam[0]), 3), round(float(p_cam[1]), 3), round(float(p_cam[2]), 3)],
            "yaw": round(math.degrees(math.atan2(float(p_cam[0]), float(p_cam[2]) + 1e-6)), 1),
            "pitch": 15.0,
            "inliers": int(np.sum(clean_masks[s_i])),
            "points": int(np.sum(clean_masks[s_i])),
            "mode": "vggt+mask2former-3d",
            "time_s": round(float(s_i) * 0.5, 2),
        }
        if clip_ids_per_frame and s_i < len(clip_ids_per_frame):
            entry["clip_id"] = clip_ids_per_frame[s_i]
        kfs_out.append(entry)

    return {
        "points": points_list,
        "keyframes": kfs_out,
        "wall_segments": wall_segments,
        "predicted_openings": predicted_openings,
        "room_envelope": {
            "room_polygon": room_polygon_world,
            "floor_y": floor_y,
            "roof_y": roof_y,
            "floor_color": [round(float(x), 3) for x in floor_fallback],
            "roof_color": [round(float(x), 3) for x in ceil_fallback],
            "wall_colors": wall_colors,
        },
        "raw_point_count": raw_cnt,
        "completed_point_count": fill_cnt,
        "semantic_model": SemanticSpatialModel.MODEL_ID,
    }


def run_semantic_spatial_pipeline(
    world_pts_gl: np.ndarray,
    imgs_rgb: np.ndarray,
    valid_masks: np.ndarray,
    cam_centers_gl: np.ndarray,
    cam_ups_gl: np.ndarray,
    clip_ids_per_frame: Optional[List[str]] = None,
    target_cam_height_m: float = 1.24,
    max_raw_points: int = 200000,
    max_fill_points: int = 160000,
    device: Optional[str] = None,
    cam_fwds_gl: Optional[np.ndarray] = None,
    imu_kf: Optional[List[Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    End-to-end 3D spatial semantic understanding, frame/clip realignment, IMU constraint
    fusion (gyro anti-bend, gravity tilt, compass/step priors), and architectural surface
    completion pipeline using Mask2Former-Swin-Large-ADE20K over VGGT-1B outputs.
    """
    cols_norm = imgs_rgb.astype(np.float64)
    if cols_norm.size > 0 and cols_norm.max() > 1.5:
        cols_norm = cols_norm / 255.0

    model = SemanticSpatialModel(device=device)
    try:
        sem = model.segment_frames(cols_norm)
    finally:
        model.unload()

    lvl_pts, lvl_cams, R_lvl_total, _ = _level_and_scale_semantic(
        world_pts_gl.astype(np.float64),
        cols_norm,
        valid_masks.astype(bool),
        sem,
        cam_centers_gl.astype(np.float64),
        cam_ups_gl.astype(np.float64),
        target_cam_height_m=target_cam_height_m,
    )
    re_pts, _ = realign_frames_semantically(lvl_pts, valid_masks.astype(bool), sem, lvl_cams)

    imu_meta: Dict[str, Any] = {"imu_used": False}
    if imu_kf:
        fwds_lvl = (cam_fwds_gl.astype(np.float64) @ R_lvl_total.T) if cam_fwds_gl is not None else None
        re_pts, lvl_cams, imu_meta = apply_imu_constraints(
            re_pts, lvl_cams, fwds_lvl, imu_kf, clip_ids_per_frame=clip_ids_per_frame
        )

    icp_pts, icp_cams = realign_clips_icp(
        re_pts, cols_norm, valid_masks.astype(bool), sem, lvl_cams, clip_ids_per_frame, imu_kf=imu_kf
    )
    res = solve_semantic_room_and_complete(
        icp_pts,
        cols_norm,
        valid_masks.astype(bool),
        sem,
        icp_cams,
        clip_ids_per_frame=clip_ids_per_frame,
        max_raw_points=max_raw_points,
        max_fill_points=max_fill_points,
    )
    if imu_meta.get("imu_used") and isinstance(res.get("room_envelope"), dict):
        res["room_envelope"]["imu"] = imu_meta
    res["imu_meta"] = imu_meta
    return res
