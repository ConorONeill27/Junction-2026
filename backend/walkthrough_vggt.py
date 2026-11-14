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
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import open3d as o3d
from PIL import Image
from scipy.spatial import cKDTree
import torch
import torchvision.transforms as TF

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


def _compute_level_transform(
    pts_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
    target_span: float = 2.8,
    cam_ups_gl: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]], np.ndarray, np.ndarray, float]:
    """
    Levels scene gravity using camera Up vectors first (never mistaking a tilted wall/desk
    for ground), then snaps to the largest horizontal floor surface below camera height.
    """
    if len(pts_np) < 50:
        return pts_np, keyframes, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64), 1.0

    pts = np.asarray(pts_np, dtype=np.float64).copy()
    cams = np.array([kf["pose"] for kf in keyframes], dtype=np.float64) if keyframes else None
    y_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)

    R0 = np.eye(3, dtype=np.float64)
    if cam_ups_gl is not None and len(cam_ups_gl) > 0:
        avg_up = np.mean(np.asarray(cam_ups_gl, dtype=np.float64), axis=0)
        n_up = float(np.linalg.norm(avg_up))
        if n_up > 1e-6:
            R0 = _rotation_matrix_a_to_b(avg_up / n_up, y_up)
            pts = pts @ R0.T
            if cams is not None and len(cams) > 0:
                cams = cams @ R0.T

    p10, p90 = np.percentile(pts, 10, axis=0), np.percentile(pts, 90, axis=0)
    span = max(0.3, float(np.linalg.norm(p90 - p10)))
    cam_y_med = float(np.median(cams[:, 1])) if cams is not None and len(cams) > 0 else float(np.percentile(pts[:, 1], 70))

    work_pcd = o3d.geometry.PointCloud()
    work_pcd.points = o3d.utility.Vector3dVector(pts)
    min_support = max(80, int(0.055 * len(pts)))

    best_score = -1.0
    best_normal = None
    best_inliers_pts = None

    for _ in range(6):
        if len(work_pcd.points) < min_support:
            break
        pm, inl = work_pcd.segment_plane(
            distance_threshold=max(0.014, 0.018 * span),
            ransac_n=3,
            num_iterations=1200,
        )
        if len(inl) < min_support:
            break
        inlier_pts = np.asarray(work_pcd.points)[inl]
        n = np.asarray(pm[:3], dtype=np.float64)
        n /= max(1e-9, float(np.linalg.norm(n)))

        plane_c = inlier_pts.mean(axis=0)
        ref_pt = cams.mean(axis=0) if cams is not None and len(cams) > 0 else pts.mean(axis=0)
        if float(np.dot(ref_pt - plane_c, n)) < 0:
            n = -n

        med_y = float(np.median(inlier_pts[:, 1]))
        # Restrict strictly to near-horizontal planes within ~27 deg of camera-up below camera Y
        if n[1] >= 0.89 and med_y <= cam_y_med + 0.05 * span:
            score = float(len(inl)) * (float(n[1]) ** 2)
            if score > best_score:
                best_score = score
                best_normal = n
                best_inliers_pts = inlier_pts
        work_pcd = work_pcd.select_by_index(inl, invert=True)

    if best_normal is not None and best_inliers_pts is not None:
        R_level = _rotation_matrix_a_to_b(best_normal, y_up)
        pts = pts @ R_level.T
        if cams is not None and len(cams) > 0:
            cams = cams @ R_level.T
        floor_y = float(np.median((best_inliers_pts @ R_level.T)[:, 1]))
    else:
        R_level = np.eye(3, dtype=np.float64)
        floor_y = float(np.percentile(pts[:, 1], 4))

    R_total = R_level @ R0
    p10_l, p90_l = np.percentile(pts, 10, axis=0), np.percentile(pts, 90, axis=0)
    span_l = max(0.3, float(np.linalg.norm(p90_l - p10_l)))
    norm_s = target_span / span_l

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

    return pts, updated_kfs, R_total, origin, norm_s


def auto_level_scene(
    pts_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
    target_span: float = 2.8,
    cam_ups_gl: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    pts, updated_kfs, _, _, _ = _compute_level_transform(
        pts_np, keyframes, target_span=target_span, cam_ups_gl=cam_ups_gl
    )
    return pts, updated_kfs


def smooth_structural_planes(pts_np: np.ndarray) -> np.ndarray:
    """
    Geometry-preserving surface smoothing: detects the dominant floor and up to 5 major
    vertical walls via RANSAC and gently pulls nearby low-residual planar points onto their
    fitted plane, removing neural depth ripple while leaving furniture and fine props sharp.
    """
    n_pts = len(pts_np)
    if n_pts < 600:
        return pts_np

    pts = np.asarray(pts_np, dtype=np.float64).copy()
    p10, p90 = np.percentile(pts, 10, axis=0), np.percentile(pts, 90, axis=0)
    span = max(0.3, float(np.linalg.norm(p90 - p10)))
    tol = max(0.008, 0.012 * span)
    floor_ceil_y = float(np.percentile(pts[:, 1], 12)) + 0.10 * span

    sub_step = max(1, n_pts // 32000)
    work = o3d.geometry.PointCloud()
    work.points = o3d.utility.Vector3dVector(pts[::sub_step])
    min_sup = max(220, int(0.038 * len(work.points)))

    claimed = np.zeros(n_pts, dtype=bool)
    planes: List[Tuple[np.ndarray, float, float]] = []
    has_floor = False

    for _ in range(7):
        if len(work.points) < min_sup or len(planes) >= 6:
            break
        pm, inl = work.segment_plane(distance_threshold=tol * 0.9, ransac_n=3, num_iterations=900)
        if len(inl) < min_sup:
            break
        nrm = np.asarray(pm[:3], dtype=np.float64)
        nlen = max(1e-9, float(np.linalg.norm(nrm)))
        nrm /= nlen
        d_val = float(pm[3]) / nlen

        abs_ny = abs(float(nrm[1]))
        if abs_ny >= 0.94:
            in_pts = np.asarray(work.points)[inl]
            med_y = float(np.median(in_pts[:, 1]))
            if not has_floor and med_y <= floor_ceil_y:
                # Flatten only the true base floor once (never create artificial terrace strata)
                n_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
                d_up = -med_y
                planes.append((n_up, d_up, 0.62))
                has_floor = True
        elif abs_ny <= 0.16:
            # Force wall plane strictly vertical (zero pitch lean) and avoid near-duplicate splits
            n_xz = np.array([nrm[0], 0.0, nrm[2]], dtype=np.float64)
            n_xz /= max(1e-9, float(np.linalg.norm(n_xz)))
            in_pts = np.asarray(work.points)[inl]
            d_xz = -float(np.median(in_pts[:, 0] * n_xz[0] + in_pts[:, 2] * n_xz[2]))
            dup = any(
                abs(float(np.dot(n_xz, pn))) > 0.97 and abs(abs(d_xz) - abs(pd)) < 3.0 * tol
                for pn, pd, _ in planes
                if abs(float(pn[1])) < 0.5
            )
            if not dup:
                planes.append((n_xz, d_xz, 0.52))
        work = work.select_by_index(inl, invert=True)

    for nrm, d_val, blend in planes:
        signed_d = pts @ nrm + d_val
        mask = (~claimed) & (np.abs(signed_d) <= tol)
        if np.any(mask):
            pts[mask] -= (blend * signed_d[mask])[:, None] * nrm[None, :]
            claimed |= mask

    return pts


def _extract_dominant_room_yaw(pts_vert_xz: np.ndarray, nrm_xz: np.ndarray) -> float:
    """Finds dominant room axis (modulo 90 deg) from vertical wall surface normals."""
    if len(nrm_xz) < 30:
        return 0.0
    ang = np.degrees(np.arctan2(nrm_xz[:, 1], nrm_xz[:, 0])) % 90.0
    hist, edges = np.histogram(ang, bins=90, range=(0.0, 90.0))
    peak = int(np.argmax(hist))
    return float(0.5 * (edges[peak] + edges[peak + 1]))


def _snap_aligned_polygon(
    poly_uv: np.ndarray,
    span_uv: float = 3.0,
    tol_deg: float = 34.0,
    colinear_cos: float = 0.985,
) -> np.ndarray:
    """
    Snaps a closed 2D polygon in dominant room (u, v) frame strictly onto straight
    orthogonal room walls (0/90 deg), rejecting sharp ~30 cm angled zig-zags and narrow
    detour teeth without ever introducing diagonal shortcuts across rooms.
    """
    if len(poly_uv) < 3:
        return poly_uv
    pts = np.asarray(poly_uv, dtype=np.float64)
    n = len(pts)
    dirs: List[np.ndarray] = []
    anchors: List[np.ndarray] = []
    edge_lens: List[float] = []
    axes = [0.0, 90.0]
    short_force_len = max(1.10, 0.45 * span_uv)
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        v = p1 - p0
        seg_l = float(np.hypot(v[0], v[1]))
        if seg_l < 1e-4:
            continue
        ang = float(np.degrees(math.atan2(v[1], v[0])) % 180.0)
        best_a = min(axes, key=lambda a: min(abs(ang - a), 180.0 - abs(ang - a)))
        diff = min(abs(ang - best_a), 180.0 - abs(ang - best_a))
        # Snap short/medium edges & near-axis walls strictly onto orthogonal 0/90 axes
        use_a = best_a if (seg_l < short_force_len or diff <= tol_deg) else ang
        du = np.array([math.cos(math.radians(use_a)), math.sin(math.radians(use_a))], dtype=np.float64)
        if np.dot(v, du) < 0:
            du = -du
        if dirs and abs(float(np.dot(dirs[-1], du))) >= colinear_cos and float(np.dot(dirs[-1], du)) > 0:
            w1, w2 = max(1e-3, edge_lens[-1]), max(1e-3, seg_l)
            anchors[-1] = (w1 * anchors[-1] + w2 * (0.5 * (p0 + p1))) / (w1 + w2)
            edge_lens[-1] += seg_l
        else:
            dirs.append(du)
            anchors.append(0.5 * (p0 + p1))
            edge_lens.append(seg_l)

    if len(dirs) >= 2 and abs(float(np.dot(dirs[0], dirs[-1]))) >= colinear_cos and float(np.dot(dirs[0], dirs[-1])) > 0:
        w1, w2 = max(1e-3, edge_lens[0]), max(1e-3, edge_lens[-1])
        anchors[0] = (w1 * anchors[0] + w2 * anchors[-1]) / (w1 + w2)
        edge_lens[0] += edge_lens[-1]
        dirs.pop()
        anchors.pop()
        edge_lens.pop()

    # Prune zero-width 180-deg needle reversals before intersecting lines
    for _ in range(4):
        if len(dirs) < 4:
            break
        removed = False
        m_d = len(dirs)
        for i in range(m_d):
            j = (i + 1) % m_d
            if float(np.dot(dirs[i], dirs[j])) < -0.96:
                drop_idx = i if edge_lens[i] <= edge_lens[j] else j
                dirs.pop(drop_idx)
                anchors.pop(drop_idx)
                edge_lens.pop(drop_idx)
                removed = True
                break
        if not removed:
            break

    # Re-merge consecutive collinear segments after needle pruning
    for _ in range(2):
        if len(dirs) < 4:
            break
        merged = False
        for i in range(len(dirs)):
            j = (i + 1) % len(dirs)
            if float(np.dot(dirs[i], dirs[j])) >= colinear_cos:
                w1, w2 = max(1e-3, edge_lens[i]), max(1e-3, edge_lens[j])
                anchors[i] = (w1 * anchors[i] + w2 * anchors[j]) / (w1 + w2)
                edge_lens[i] += edge_lens[j]
                dirs.pop(j)
                anchors.pop(j)
                edge_lens.pop(j)
                merged = True
                break
        if not merged:
            break

    m = len(dirs)
    if m < 3:
        return pts
    # Intersect adjacent snapped infinite lines so consecutive walls share exact watertight corners
    corners: List[np.ndarray] = []
    for i in range(m):
        j = (i + 1) % m
        d1, p1, l1 = dirs[i], anchors[i], edge_lens[i]
        d2, p2, l2 = dirs[j], anchors[j], edge_lens[j]
        det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
        if abs(det) < 1e-4:
            corners.append(0.5 * (p1 + p2))
        else:
            rhs = p2 - p1
            t = (rhs[0] * (-d2[1]) - rhs[1] * (-d2[0])) / det
            inter = p1 + t * d1
            max_reach = max(2.0, 2.0 * (l1 + l2))
            if float(np.linalg.norm(inter - 0.5 * (p1 + p2))) > max_reach:
                corners.append(0.5 * (p1 + p2))
            else:
                corners.append(inter)
    out = np.asarray(corners, dtype=np.float64)

    # Collapse tiny narrow U-turn teeth & small wall discretization steps strictly orthogonally
    min_tooth = max(0.28, 0.06 * span_uv)
    min_zstep = max(0.20, 0.04 * span_uv)
    for _ in range(6):
        if len(out) <= 4:
            break
        changed = False
        k = len(out)
        for i in range(k):
            p_prev2 = out[(i - 2) % k]
            p_prev = out[(i - 1) % k]
            p_cur = out[i]
            p_nxt = out[(i + 1) % k]
            p_after = out[(i + 2) % k]
            p_after2 = out[(i + 3) % k]
            e_in = p_cur - p_prev
            e_step = p_nxt - p_cur
            e_out = p_after - p_nxt
            l_in = float(np.hypot(e_in[0], e_in[1]))
            l_step = float(np.hypot(e_step[0], e_step[1]))
            l_out = float(np.hypot(e_out[0], e_out[1]))
            if l_step < 0.04:
                out = np.delete(out, i, axis=0)
                changed = True
                break
            if l_in < 1e-4 or l_out < 1e-4:
                continue
            u_in, u_step, u_out = e_in / l_in, e_step / l_step, e_out / l_out
            # Only simplify perpendicular orthogonal steps where e_step ITSELF is short
            if abs(float(np.dot(u_in, u_step))) > 0.12 or abs(float(np.dot(u_out, u_step))) > 0.12:
                continue
            dot_io = float(np.dot(u_in, u_out))
            # 1) Narrow U-turn fin/tooth (e_step is small and u_in opposes u_out): shift corner orthogonally
            if dot_io < -0.95 and l_step < min_tooth and k >= 6:
                e_before = p_prev - p_prev2
                e_after = p_after2 - p_after
                lb, la = float(np.hypot(e_before[0], e_before[1])), float(np.hypot(e_after[0], e_after[1]))
                if l_in <= l_out and lb > 1e-4 and abs(float(np.dot(e_before / lb, u_step))) > 0.95:
                    if float(np.dot(e_before / lb, u_step)) > 0 or lb > l_step + 0.04:
                        q = p_prev + e_step
                        out[i] = q
                        out = np.delete(out, [(i - 1) % k, (i + 1) % k], axis=0)
                        changed = True
                        break
                elif l_out < l_in and la > 1e-4 and abs(float(np.dot(e_after / la, u_step))) > 0.95:
                    if float(np.dot(e_after / la, -u_step)) > 0 or la > l_step + 0.04:
                        q = p_after - e_step
                        out[i] = q
                        out = np.delete(out, [(i + 1) % k, (i + 2) % k], axis=0)
                        changed = True
                        break
            # 2) Tiny Z-shaped staircase step along the same wall direction: align shorter step onto longer wall
            elif dot_io > 0.95 and l_step < min_zstep and k >= 6:
                e_before = p_prev - p_prev2
                e_after = p_after2 - p_after
                lb, la = float(np.hypot(e_before[0], e_before[1])), float(np.hypot(e_after[0], e_after[1]))
                if l_in >= l_out and la > 1e-4 and abs(float(np.dot(e_after / la, u_step))) > 0.95:
                    if float(np.dot(e_after / la, u_step)) > 0 or la > l_step + 0.04:
                        q = p_after - e_step
                        out[(i + 2) % k] = q
                        out = np.delete(out, [i, (i + 1) % k], axis=0)
                        changed = True
                        break
                elif l_out > l_in and lb > 1e-4 and abs(float(np.dot(e_before / lb, u_step))) > 0.95:
                    if float(np.dot(e_before / lb, -u_step)) > 0 or lb > l_step + 0.04:
                        q = p_prev + e_step
                        out[(i - 1) % k] = q
                        out = np.delete(out, [i, (i + 1) % k], axis=0)
                        changed = True
                        break
        if not changed:
            break

    # Final pass: remove redundant collinear vertices along straight wall segments
    for _ in range(4):
        if len(out) < 4:
            break
        k = len(out)
        dropped = False
        for i in range(k):
            v1 = out[i] - out[(i - 1) % k]
            v2 = out[(i + 1) % k] - out[i]
            l1, l2 = float(np.hypot(v1[0], v1[1])), float(np.hypot(v2[0], v2[1]))
            if l1 < 0.04 or l2 < 0.04 or float(np.dot(v1 / max(1e-6, l1), v2 / max(1e-6, l2))) > 0.999:
                out = np.delete(out, i, axis=0)
                dropped = True
                break
        if not dropped:
            break
    return out if len(out) >= 3 else pts


def predict_room_envelope(
    pts_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Continuous Room Architecture Solver assuming indoor spaces have:
    - Continuous enclosing walls (preserving scanned pillars, alcoves & L-corners,
      and closing unobserved walls behind the camera along dominant room axes),
    - A full continuous horizontal floor plane (floor_y ~ 0),
    - A full continuous horizontal roof/ceiling plane (roof_y).

    Returns {
      "wall_segments": [[x1, z1, x2, z2, floor_y, roof_y, is_observed], ...],
      "predicted_openings": [[x1, z1, x2, z2, floor_y, h_door], ...],
      "room_polygon": [[x, z], ...],
      "floor_y": float,
      "roof_y": float,
    }.
    """
    empty = {
        "wall_segments": [],
        "predicted_openings": [],
        "room_polygon": [],
        "floor_y": 0.0,
        "roof_y": 1.0,
    }
    if len(pts_np) < 250 or not keyframes:
        return empty

    pts = np.asarray(pts_np, dtype=np.float64)
    cams = np.array([k["pose"] for k in keyframes], dtype=np.float64)

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.07, max_nn=24))
    nrm = np.asarray(pcd.normals)

    y_low_pct = float(np.percentile(pts[:, 1], 5))
    y_low = max(0.05, y_low_pct + 0.04)
    y_top = float(np.percentile(pts[:, 1], 98))
    if y_top <= y_low + 0.08:
        return empty

    is_vert = (np.abs(nrm[:, 1]) < 0.34) & (pts[:, 1] > y_low)
    is_floor = (np.abs(nrm[:, 1]) >= 0.84) & (pts[:, 1] <= y_low + 0.10)
    floor_y = round(float(np.clip(np.median(pts[is_floor, 1]) if np.any(is_floor) else max(0.0, y_low_pct), -0.06, 0.04)), 3)

    cam_p90_y = float(np.percentile(cams[:, 1], 90))
    ceil_mask = (np.abs(nrm[:, 1]) >= 0.78) & (pts[:, 1] >= max(cam_p90_y * 1.15, 0.74 * y_top))
    if int(np.count_nonzero(ceil_mask)) >= 80:
        roof_obs = float(np.percentile(pts[ceil_mask, 1], 55))
    else:
        roof_obs = y_top
    roof_y = round(float(max(roof_obs, cam_p90_y * 1.32, y_top * 1.02, floor_y + 0.75)), 3)

    # Rotate XZ into room-aligned coordinates so inferred walls close at true architectural angles
    dom_deg = _extract_dominant_room_yaw(pts[is_vert][:, [0, 2]], nrm[is_vert][:, [0, 2]]) if np.any(is_vert) else 0.0
    c_d, s_d = math.cos(math.radians(dom_deg)), math.sin(math.radians(dom_deg))
    R_to_dom = np.array([[c_d, s_d], [-s_d, c_d]], dtype=np.float64)

    pts_uv = pts[:, [0, 2]] @ R_to_dom.T
    cams_uv = cams[:, [0, 2]] @ R_to_dom.T

    umin = float(min(np.percentile(pts_uv[:, 0], 1), cams_uv[:, 0].min()))
    umax = float(max(np.percentile(pts_uv[:, 0], 99), cams_uv[:, 0].max()))
    vmin = float(min(np.percentile(pts_uv[:, 1], 1), cams_uv[:, 1].min()))
    vmax = float(max(np.percentile(pts_uv[:, 1], 99), cams_uv[:, 1].max()))
    span_uv = max(0.6, float(max(umax - umin, vmax - vmin)))

    G = 144
    pad_world = 0.10 * span_uv
    umin -= pad_world
    vmin -= pad_world
    cell = (span_uv + 2.0 * pad_world) / (G - 12)
    origin_u = umin - 6.0 * cell
    origin_v = vmin - 6.0 * cell

    iu = np.clip(((pts_uv[:, 0] - origin_u) / cell).astype(np.int32), 1, G - 2)
    iv = np.clip(((pts_uv[:, 1] - origin_v) / cell).astype(np.int32), 1, G - 2)

    vert_cnt = np.zeros((G, G), dtype=np.int32)
    high_cnt = np.zeros((G, G), dtype=np.int32)
    floor_cnt = np.zeros((G, G), dtype=np.int32)
    max_y = np.full((G, G), -1e9, dtype=np.float64)

    np.add.at(vert_cnt, (iu[is_vert], iv[is_vert]), 1)
    np.add.at(high_cnt, (iu[is_vert & (pts[:, 1] > 0.28 * y_top)], iv[is_vert & (pts[:, 1] > 0.28 * y_top)]), 1)
    np.add.at(floor_cnt, (iu[is_floor], iv[is_floor]), 1)
    np.maximum.at(max_y, (iu[is_vert], iv[is_vert]), pts[is_vert, 1])

    # Structural walls: ignore low furniture/monitors inside seen floor so furniture never slices a room
    min_wall_h = max(0.34, 0.60 * max(y_top, cam_p90_y))
    wall_raw = ((vert_cnt >= 4) & (high_cnt >= 2) & (max_y >= min_wall_h)).astype(np.uint8)
    n_wl, wl_lbls, wl_stats, _ = cv2.connectedComponentsWithStats(wall_raw)
    wall_solid = np.zeros_like(wall_raw)
    wall_macro = np.zeros_like(wall_raw)
    min_macro_diag = max(10.0, 0.24 * (span_uv / cell))
    for l_id in range(1, n_wl):
        if wl_stats[l_id, cv2.CC_STAT_AREA] >= 6:
            wall_solid[wl_lbls == l_id] = 1
            bw = float(wl_stats[l_id, cv2.CC_STAT_WIDTH])
            bh = float(wl_stats[l_id, cv2.CC_STAT_HEIGHT])
            if math.hypot(bw, bh) >= min_macro_diag:
                wall_macro[wl_lbls == l_id] = 1
    wall_barrier = cv2.morphologyEx(wall_macro, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    seen_int = ((floor_cnt >= 2) & (wall_barrier == 0)).astype(np.uint8)
    cam_cells = np.column_stack([
        np.clip(((cams_uv[:, 0] - origin_u) / cell).astype(np.int32), 2, G - 3),
        np.clip(((cams_uv[:, 1] - origin_v) / cell).astype(np.int32), 2, G - 3),
    ])
    cam_path = np.zeros((G, G), dtype=np.uint8)
    cam_r = max(3, int(0.055 * span_uv / cell))
    for i in range(len(cam_cells)):
        c0 = (int(cam_cells[i, 1]), int(cam_cells[i, 0]))
        cv2.circle(seen_int, c0, cam_r, 1, -1)
        cv2.circle(cam_path, c0, cam_r, 1, -1)
        cv2.circle(wall_barrier, c0, 2, 0, -1)
        if i > 0:
            c1 = (int(cam_cells[i - 1, 1]), int(cam_cells[i - 1, 0]))
            cv2.line(seen_int, c1, c0, 1, thickness=max(3, cam_r))
            cv2.line(cam_path, c1, c0, 1, thickness=max(3, cam_r))
            cv2.line(wall_barrier, c1, c0, 0, thickness=2)

    seen_int = cv2.morphologyEx(seen_int, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    # Build oriented room bounding box from walked floor, camera trajectory, and nearby walls
    adj_walls = wall_barrier & cv2.dilate(seen_int, np.ones((13, 13), np.uint8))
    core_mask = (seen_int > 0) | (cam_path > 0) | (adj_walls > 0)
    core_u, core_v = np.where(core_mask)
    if len(core_u) < 4:
        return empty

    u0_box = max(2, int(core_u.min()) - 2)
    u1_box = min(G - 3, int(core_u.max()) + 2)
    v0_box = max(2, int(core_v.min()) - 2)
    v1_box = min(G - 3, int(core_v.max()) + 2)

    grid_u, grid_v = np.meshgrid(
        np.arange(u0_box, u1_box + 1, dtype=np.float64),
        np.arange(v0_box, v1_box + 1, dtype=np.float64),
        indexing="ij",
    )
    box_cells = np.column_stack([grid_u.ravel(), grid_v.ravel()])  # (M, 2)

    # Visibility carve: when the camera walked through the room, cells occluded behind observed
    # walls from ALL poses lie outside (carving alcoves/L-corners). Single-spot scans skip radial ray fans.
    cam_span_uv = float(np.linalg.norm(cams_uv.max(axis=0) - cams_uv.min(axis=0))) if len(cams_uv) > 1 else 0.0
    if cam_span_uv >= 0.14 * span_uv and np.any(wall_barrier):
        k_idx = np.linspace(0, len(cam_cells) - 1, min(len(cam_cells), 14), dtype=int)
        ray_cams = cam_cells[k_idx].astype(np.float64)  # (K, 2)
        t_steps = np.linspace(0.06, 0.88, 22, dtype=np.float64)[None, None, :]  # (1, 1, T)
        ru = ray_cams[None, :, 0:1] + t_steps * (box_cells[:, None, 0:1] - ray_cams[None, :, 0:1])
        rv = ray_cams[None, :, 1:2] + t_steps * (box_cells[:, None, 1:2] - ray_cams[None, :, 1:2])
        ru_i = np.clip(np.rint(ru).astype(np.int32), 0, G - 1)
        rv_i = np.clip(np.rint(rv).astype(np.int32), 0, G - 1)
        blocked = np.any(wall_barrier[ru_i, rv_i] > 0, axis=2)  # (M, K)
        visible_any = ~np.all(blocked, axis=1)
    else:
        visible_any = np.ones(len(box_cells), dtype=bool)

    room_mask = np.zeros((G, G), dtype=np.uint8)
    bu = box_cells[visible_any, 0].astype(np.int32)
    bv = box_cells[visible_any, 1].astype(np.int32)
    room_mask[bu, bv] = 255
    # Guarantee full walked floor and camera path stay strictly enclosed inside the room
    room_mask[cam_path > 0] = 255
    room_mask[(seen_int > 0) & (wall_barrier == 0)] = 255
    room_mask = cv2.morphologyEx(room_mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    # Keep only the main connected enclosed room component and fill tiny interior ray-gaps
    n_rm, rm_lbls, rm_stats, _ = cv2.connectedComponentsWithStats(room_mask)
    if n_rm <= 1:
        return empty
    best_rm = 1 + int(np.argmax(rm_stats[1:, cv2.CC_STAT_AREA]))
    clean_room = (rm_lbls == best_rm).astype(np.uint8) * 255
    # Fill tiny interior floor pinholes without bridging exterior L-recesses
    inv_room = 255 - clean_room
    n_holes, h_lbls, h_stats, _ = cv2.connectedComponentsWithStats(inv_room)
    for hid in range(1, n_holes):
        # Connected component touching image border (lbl at [0,0]) is exterior; interior small holes get filled
        if hid != h_lbls[0, 0] and h_stats[hid, cv2.CC_STAT_AREA] < 36:
            clean_room[h_lbls == hid] = 255

    # Only punch substantial freestanding full-height interior columns (measured along true minor axis)
    ext_near = cv2.dilate((clean_room == 0).astype(np.uint8), np.ones((21, 21), np.uint8)) > 0
    pillar_only = ((wall_solid > 0) & (clean_room > 0) & (seen_int == 0) & (max_y >= 0.78 * roof_y)).astype(np.uint8) * 255
    if np.any(pillar_only):
        n_pl, pl_lbls, pl_stats, _ = cv2.connectedComponentsWithStats(pillar_only)
        cell_area = cell * cell
        for pl_id in range(1, n_pl):
            area_cells = int(pl_stats[pl_id, cv2.CC_STAT_AREA])
            if area_cells * cell_area < 0.14:
                continue
            comp_m = (pl_lbls == pl_id)
            if np.any(ext_near[comp_m]):
                continue
            ys_c, xs_c = np.where(comp_m)
            rect_dims = cv2.minAreaRect(np.column_stack([xs_c, ys_c]).astype(np.float32))[1]
            minor_m = float(min(rect_dims[0], rect_dims[1])) * cell
            if minor_m >= 0.30:
                clean_room[comp_m] = 0

    wall_zone = cv2.dilate(wall_solid | wall_barrier, np.ones((11, 11), np.uint8))
    seen_zone = cv2.dilate(seen_int | cam_path, np.ones((7, 7), np.uint8))

    wall_segments: List[List[float]] = []
    predicted_openings: List[List[float]] = []
    room_polygon_world: List[List[float]] = []

    contours, hierarchy = cv2.findContours(clean_room, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not contours or hierarchy is None:
        return empty
    hier = hierarchy[0]

    # Pick largest top-level outer room perimeter contour (+ any interior pillar holes)
    outer_idxs = [i for i in range(len(contours)) if hier[i][3] == -1]
    if not outer_idxs:
        return empty
    main_idx = max(outer_idxs, key=lambda idx: cv2.contourArea(contours[idx]))

    for c_idx in range(len(contours)):
        is_outer = (c_idx == main_idx)
        is_hole = (hier[c_idx][3] == main_idx) and (cv2.contourArea(contours[c_idx]) * (cell * cell) >= 0.14)
        if not (is_outer or is_hole):
            continue
        cnt = contours[c_idx]
        if len(cnt) < 3:
            continue
        # Convert image (col=v_idx, row=u_idx) -> aligned (u, v)
        pts_uv_cnt = np.column_stack([
            origin_u + (cnt[:, 0, 1] + 0.5) * cell,
            origin_v + (cnt[:, 0, 0] + 0.5) * cell,
        ]).astype(np.float32).reshape(-1, 1, 2)
        eps_poly = max(0.075, (0.026 if is_outer else 0.024) * span_uv)
        simp_uv = cv2.approxPolyDP(pts_uv_cnt, epsilon=eps_poly, closed=True).reshape(-1, 2)
        snapped_uv = _snap_aligned_polygon(simp_uv, span_uv=span_uv)
        poly_world = snapped_uv @ R_to_dom  # R_to_world.T == R_to_dom for row vecs

        if is_outer:
            room_polygon_world = [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in poly_world]

        m_v = len(poly_world)
        for i in range(m_v):
            p1, p2 = poly_world[i], poly_world[(i + 1) % m_v]
            seg_len = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
            if seg_len < 0.06:
                continue
            # Sample along the entire edge to classify observed wall vs inferred enclosure & doorways
            n_samp = max(5, int(seg_len / max(0.05, cell * 1.8)))
            ts = np.linspace(0.08, 0.92, n_samp)
            hit_wall, hit_seen = 0, 0
            for tt in ts:
                su_v = (1.0 - tt) * snapped_uv[i] + tt * snapped_uv[(i + 1) % m_v]
                si = int(np.clip((su_v[0] - origin_u) / cell, 0, G - 1))
                sj = int(np.clip((su_v[1] - origin_v) / cell, 0, G - 1))
                if wall_zone[si, sj] > 0:
                    hit_wall += 1
                elif seen_zone[si, sj] > 0:
                    hit_seen += 1
            obs_wall = 1.0 if hit_wall >= max(2, int(0.28 * n_samp)) else 0.0

            # Always emit the continuous enclosing wall (observed + predicted closure wall)
            wall_segments.append([
                round(float(p1[0]), 4), round(float(p1[1]), 4),
                round(float(p2[0]), 4), round(float(p2[1]), 4),
                floor_y, roof_y, obs_wall,
            ])

            # Where walked interior reaches an unobserved outer boundary edge, emit predicted exit/doorway
            if is_outer and obs_wall == 0.0 and seg_len >= max(0.24, 0.08 * span_uv):
                if hit_seen >= max(2, int(0.45 * n_samp)):
                    predicted_openings.append([
                        round(float(p1[0]), 4), round(float(p1[1]), 4),
                        round(float(p2[0]), 4), round(float(p2[1]), 4),
                        floor_y, round(floor_y + 0.82 * (roof_y - floor_y), 3),
                    ])

    return {
        "wall_segments": wall_segments,
        "predicted_openings": predicted_openings,
        "room_polygon": room_polygon_world,
        "floor_y": floor_y,
        "roof_y": roof_y,
    }


def predict_wall_architecture(
    pts_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
) -> Tuple[List[List[float]], List[List[float]]]:
    """Compatibility wrapper returning (wall_segments, predicted_openings) from predict_room_envelope."""
    env = predict_room_envelope(pts_np, keyframes)
    return env["wall_segments"], env["predicted_openings"]


def _points_in_poly_dist(xz: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """
    Fast vectorized signed-ish margin check for 2D polygon containment:
    returns True for points strictly inside OR within edge distance <= margin (checked separately).
    """
    x, z = xz[:, 0], xz[:, 1]
    n = len(poly)
    inside = np.zeros(len(xz), dtype=bool)
    px, pz = poly[:, 0], poly[:, 1]
    j = n - 1
    for i in range(n):
        cond = (pz[i] > z) != (pz[j] > z)
        # Safe division when pz[j] == pz[i] since cond is False there
        x_int = (px[j] - px[i]) * (z - pz[i]) / np.where(cond, pz[j] - pz[i], 1.0) + px[i]
        inside ^= cond & (x < x_int)
        j = i
    return inside


def _min_edge_dist_2d(xz: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Minimum Euclidean distance from each 2D point to closed polygon edges."""
    n = len(poly)
    min_d2 = np.full(len(xz), np.inf, dtype=np.float64)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        ab = b - a
        ab2 = float(ab[0] * ab[0] + ab[1] * ab[1])
        if ab2 < 1e-12:
            d2 = np.sum((xz - a[None, :]) ** 2, axis=1)
        else:
            t = np.clip(((xz[:, 0] - a[0]) * ab[0] + (xz[:, 1] - a[1]) * ab[1]) / ab2, 0.0, 1.0)
            proj_x = a[0] + t * ab[0]
            proj_z = a[1] + t * ab[1]
            d2 = (xz[:, 0] - proj_x) ** 2 + (xz[:, 1] - proj_z) ** 2
        min_d2 = np.minimum(min_d2, d2)
    return np.sqrt(min_d2)


def enforce_room_envelope(
    pts_np: np.ndarray,
    cols_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Applies the physical room assumption (continuous enclosing walls, full floor, full roof):
    solves predict_room_envelope, prunes physically impossible ghost points outside the closed
    room walls or beyond [floor_y, roof_y] tolerance, and returns (pts_np, cols_np, room_env).
    """
    env = predict_room_envelope(pts_np, keyframes) if len(pts_np) > 0 else predict_room_envelope(np.zeros((0, 3)), [])
    poly = env.get("room_polygon", [])
    if len(pts_np) < 250 or len(poly) < 3:
        return pts_np, cols_np, env

    poly_arr = np.asarray(poly, dtype=np.float64)
    span_xz = max(0.6, float(max(np.ptp(poly_arr[:, 0]), np.ptp(poly_arr[:, 1]))))
    margin_xz = max(0.08, 0.03 * span_xz)
    fy, ry = float(env["floor_y"]), float(env["roof_y"])

    inside = _points_in_poly_dist(pts_np[:, [0, 2]], poly_arr)
    edge_d = np.where(inside, 0.0, _min_edge_dist_2d(pts_np[:, [0, 2]], poly_arr))
    keep = (
        (edge_d <= margin_xz)
        & (pts_np[:, 1] >= fy - 0.06)
        & (pts_np[:, 1] <= ry + 0.06)
    )
    if int(np.count_nonzero(keep)) >= max(250, int(0.65 * len(pts_np))):
        pruned_frac = 1.0 - (float(np.count_nonzero(keep)) / max(1, len(pts_np)))
        pts_np = pts_np[keep]
        cols_np = cols_np[keep]
        if pruned_frac > 0.015:
            env = predict_room_envelope(pts_np, keyframes)
    return pts_np, cols_np, env


def predict_wall_segments(
    pts_np: np.ndarray,
    keyframes: List[Dict[str, Any]],
) -> List[List[float]]:
    walls, _ = predict_wall_architecture(pts_np, keyframes)
    return walls


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


def _umeyama_sim3_ransac(src: np.ndarray, dst: np.ndarray, iters: int = 40) -> Tuple[float, np.ndarray, np.ndarray]:
    """RANSAC-hardened Umeyama Sim(3) rejecting noisy depth/edge correspondences."""
    n = len(src)
    if n < 18:
        return _umeyama_sim3(src, dst)

    rng = np.random.default_rng(42)
    dst_span = max(0.2, float(np.linalg.norm(np.percentile(dst, 90, axis=0) - np.percentile(dst, 10, axis=0))))
    tol = max(0.015, 0.03 * dst_span)

    best_inliers = None
    best_count = -1
    for _ in range(iters):
        idx = rng.choice(n, size=6, replace=False)
        s, R, t = _umeyama_sim3(src[idx], dst[idx])
        pred = s * (src @ R.T) + t
        err = np.linalg.norm(pred - dst, axis=1)
        inl = err < tol
        cnt = int(np.sum(inl))
        if cnt > best_count:
            best_count = cnt
            best_inliers = inl

    if best_inliers is not None and best_count >= 12:
        return _umeyama_sim3(src[best_inliers], dst[best_inliers])
    return _umeyama_sim3(src, dst)


def extract_sharp_keyframes(
    video_path: str,
    out_dir: Path,
    target_segments: int = 28,
    max_keyframes: int = 24,
    imu: Optional[Dict[str, np.ndarray]] = None,
    imu_offset_s: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Single sequential pass over the video using grab() + retrieve() on ~5 candidate frames
    per temporal segment, picking only the sharpest, low-motion keyframes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    if total <= 0:
        raise ValueError(f"Could not read frames from {video_path}")

    has_imu = imu is not None and len(imu.get("t_s", [])) > 0
    imu_t = imu["t_s"] if has_imu else None
    imu_w = imu["omega_norm"] if has_imu else None

    sample_stride = max(1, total // (target_segments * 5))
    seg_len = max(1, total // target_segments)
    seg_best: Dict[int, Tuple[int, float, float, np.ndarray]] = {}

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
                score = sharp
                if has_imu and imu_t is not None and imu_w is not None:
                    t_frame = (idx / max(1.0, fps)) + imu_offset_s
                    omega = float(np.interp(t_frame, imu_t, imu_w))
                    score = sharp / (1.0 + (omega / 35.0) ** 2)
                seg_i = min(target_segments - 1, idx // seg_len)
                if seg_i not in seg_best or score > seg_best[seg_i][1]:
                    # Downscale early to save RAM
                    h, w = frame.shape[:2]
                    sc = min(1.0, 960.0 / max(h, w))
                    if sc < 1.0:
                        frame = cv2.resize(frame, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA)
                    seg_best[seg_i] = (idx, score, sharp, frame)
        idx += 1
    cap.release()

    candidates = [(s_i, v[0], v[1], v[2], v[3]) for s_i, v in sorted(seg_best.items())]
    if not candidates:
        return []

    med_sharp = float(np.median([c[3] for c in candidates]))
    min_sharp = max(12.0, med_sharp * 0.35)

    # Filter blurred segments, keeping best up to max_keyframes
    kept = [c for c in candidates if c[3] >= min_sharp]
    if len(kept) < 6:
        kept = sorted(candidates, key=lambda x: -x[2])[:min(len(candidates), 12)]
        kept = sorted(kept, key=lambda x: x[1])
    elif len(kept) > max_keyframes:
        # Subsample evenly across time
        sel = np.linspace(0, len(kept) - 1, max_keyframes, dtype=int)
        kept = [kept[i] for i in sel]

    saved: List[Dict[str, Any]] = []
    for k, (_, frame_idx, _, sharp, frame_bgr) in enumerate(kept):
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

    @staticmethod
    def _load_uniform_tensors(image_paths: List[str], target_w: int = 364) -> torch.Tensor:
        """Loads frames preserving portrait/landscape FOV at VGGT-aligned 14px strides."""
        resized: List[Tuple[int, int, Image.Image]] = []
        heights: List[int] = []
        for p in image_paths:
            im = Image.open(p).convert("RGB")
            w, h = im.size
            th = max(14, int(round(h * (target_w / max(1, w)) / 14)) * 14)
            im_r = im.resize((target_w, th), Image.Resampling.BICUBIC)
            resized.append((target_w, th, im_r))
            heights.append(th)

        target_h = int(round(float(np.median(heights)) / 14.0)) * 14
        target_h = max(14, target_h)
        tensors: List[torch.Tensor] = []
        for _, th, im_r in resized:
            t = TF.ToTensor()(im_r)
            if th > target_h:
                top = (th - target_h) // 2
                t = t[:, top:top + target_h, :]
            elif th < target_h:
                pad_top = (target_h - th) // 2
                pad_bot = target_h - th - pad_top
                t = torch.nn.functional.pad(t, (0, 0, pad_top, pad_bot), value=0.0)
            tensors.append(t)
        return torch.stack(tensors)

    def _infer_raw(
        self,
        image_paths: List[str],
        target_w: int = 364,
    ) -> Dict[str, Any]:
        from vggt.utils.pose_enc import pose_encoding_to_extri_intri
        from vggt.utils.geometry import unproject_depth_map_to_point_map

        self._load_model()
        if self._device == "cuda":
            torch.cuda.empty_cache()

        images = self._load_uniform_tensors(image_paths, target_w=target_w).to(self._device)
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
        depth = preds["depth"].float().cpu().numpy().squeeze(0)
        depth_conf = preds["depth_conf"].float().cpu().numpy().squeeze(0)
        extri_np = extrinsic.float().cpu().numpy().squeeze(0)
        intri_np = intrinsic.float().cpu().numpy().squeeze(0)
        imgs_np = images.float().cpu().numpy()
        if imgs_np.ndim == 5:
            imgs_np = imgs_np.squeeze(0)
        imgs_rgb = np.transpose(imgs_np, (0, 2, 3, 1))
        del preds, images, extrinsic, intrinsic
        if self._device == "cuda":
            torch.cuda.empty_cache()

        world_pts = unproject_depth_map_to_point_map(depth, extri_np, intri_np)
        S = world_pts.shape[0]
        cam_centers = np.zeros((S, 3), dtype=np.float64)
        cam_ups = np.zeros((S, 3), dtype=np.float64)
        cam_fwds = np.zeros((S, 3), dtype=np.float64)
        for s_i in range(S):
            R_w2c = extri_np[s_i, :3, :3]
            t_w2c = extri_np[s_i, :3, 3]
            cam_centers[s_i] = -(R_w2c.T @ t_w2c)
            cam_ups[s_i] = -extri_np[s_i, 1, :3]
            cam_fwds[s_i] = extri_np[s_i, 2, :3]

        return {
            "world_pts": world_pts,
            "depth": depth,
            "depth_conf": depth_conf,
            "extrinsics": extri_np,
            "intrinsics": intri_np,
            "imgs_rgb": imgs_rgb,
            "cam_centers": cam_centers,
            "cam_ups": cam_ups,
            "cam_fwds": cam_fwds,
        }

    def _compute_clean_masks(
        self,
        raw: Dict[str, Any],
        conf_percentile: float = 42.0,
        min_views: Optional[int] = None,
    ) -> np.ndarray:
        """
        Multi-view RGB+depth consistency + free-space ray-carving + dilated edge strip.
        Eliminates glass/window reflections, halo wings, and moving foreground hands/people.
        """
        world_pts = raw["world_pts"]
        depth = raw["depth"]
        depth_conf = raw["depth_conf"]
        ext_np = raw["extrinsics"]
        int_np = raw["intrinsics"]
        rgb = raw["imgs_rgb"]
        cam_centers = raw["cam_centers"]
        S, H, W, _ = world_pts.shape

        cam_span = float(np.linalg.norm(cam_centers.max(axis=0) - cam_centers.min(axis=0))) if S > 1 else 0.0
        d_valid = depth[np.isfinite(depth) & (depth > 0.05)]
        med_d = float(np.median(d_valid)) if len(d_valid) else 1.5
        is_stationary = cam_span < 0.15
        max_d_clip = min(
            float(np.percentile(d_valid, 92 if is_stationary else 95)) if len(d_valid) else 10.0,
            med_d * (2.3 if is_stationary else 3.4),
        )
        req_views = min_views if min_views is not None else (1 if is_stationary else (2 if S >= 8 else 1))

        valid_masks = np.zeros((S, H, W), dtype=bool)
        for s_i in range(S):
            d_i = depth[s_i, :, :, 0]
            c_i = depth_conf[s_i]
            edge = utils3d.np.depth_map_edge(d_i, rtol=0.022)
            edge = cv2.dilate(edge.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            p_i = world_pts[s_i]
            graze = np.zeros((H, W), dtype=bool)
            if H > 2 and W > 2:
                dx = p_i[1:-1, 2:] - p_i[1:-1, :-2]
                dy = p_i[2:, 1:-1] - p_i[:-2, 1:-1]
                nrm = np.cross(dx, dy)
                nrm_l = np.linalg.norm(nrm, axis=2, keepdims=True) + 1e-8
                rays = p_i[1:-1, 1:-1] - cam_centers[s_i]
                ray_l = np.linalg.norm(rays, axis=2, keepdims=True) + 1e-8
                cos_i = np.abs(np.sum((nrm / nrm_l) * (rays / ray_l), axis=2))
                graze[1:-1, 1:-1] = cos_i < 0.26

            th_low = float(np.percentile(c_i, min(22.0, conf_percentile * 0.5)))
            cand_m = (~edge) & (~graze) & (d_i > 0.06) & (d_i < max_d_clip) & (c_i >= th_low) & np.isfinite(d_i)
            p_cand = p_i[cand_m]
            c_cand = rgb[s_i][cand_m]
            cf_cand = c_i[cand_m]
            if len(p_cand) == 0:
                continue

            if S >= 3:
                support = np.zeros(len(p_cand), dtype=np.int32)
                viol = np.zeros(len(p_cand), dtype=bool)
                for j in range(S):
                    if j == s_i:
                        continue
                    pj = (p_cand @ ext_np[j, :3, :3].T) + ext_np[j, :3, 3]
                    zj = pj[:, 2]
                    front = zj > 0.08
                    if not np.any(front):
                        continue
                    idx_f = np.where(front)[0]
                    uv = pj[front] @ int_np[j].T
                    u = np.round(uv[:, 0] / zj[front]).astype(int)
                    v = np.round(uv[:, 1] / zj[front]).astype(int)
                    inb = (u >= 0) & (u < W) & (v >= 0) & (v < H)
                    if not np.any(inb):
                        continue
                    idx_in = idx_f[inb]
                    uin, vin, zin = u[inb], v[inb], zj[front][inb]
                    obs_z = depth[j, vin, uin, 0]
                    obs_rgb = rgb[j, vin, uin]
                    ok = (np.abs(zin - obs_z) < np.maximum(0.05, 0.08 * obs_z)) & (
                        np.linalg.norm(c_cand[idx_in] - obs_rgb, axis=1) < 0.20
                    )
                    support[idx_in[ok]] += 1
                    bad = (zin < obs_z * 0.87) & ((obs_z - zin) > 0.10) & (depth_conf[j, vin, uin] >= 1.01)
                    viol[idx_in[bad]] = True

                p80_c = max(1.55, float(np.percentile(c_i, 85)))
                keep = (~viol) & ((support >= req_views) | ((cf_cand >= p80_c) & (support >= max(0, req_views - 1))))
                if int(np.count_nonzero(keep)) < 400:
                    keep = ~viol
            else:
                keep = cf_cand >= float(np.percentile(c_i, conf_percentile))

            ys, xs = np.where(cand_m)
            valid_masks[s_i, ys[keep], xs[keep]] = True

        return valid_masks

    def _infer_window(
        self, image_paths: List[str], conf_percentile: float = 42.0
    ) -> Dict[str, Any]:
        raw = self._infer_raw(image_paths, target_w=364)
        valid_masks = self._compute_clean_masks(raw, conf_percentile=conf_percentile)
        return {
            "world_pts": raw["world_pts"],
            "depth_conf": raw["depth_conf"],
            "valid_masks": valid_masks,
            "imgs_rgb": raw["imgs_rgb"],
            "cam_centers": raw["cam_centers"],
            "cam_ups": raw["cam_ups"],
            "extrinsics": raw["extrinsics"],
        }

    def reconstruct_frames_global(
        self,
        image_paths: List[str],
        output_json: Optional[Path] = None,
        keyframes_meta: Optional[List[Dict[str, Any]]] = None,
        max_points: int = 260000,
        target_span: float = 2.8,
        clip_ids_per_frame: Optional[List[str]] = None,
        anchor_dir: Optional[Path] = None,
        use_spatial_model: bool = True,
        imu_kf: Optional[List[Optional[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """
        Single-pass global VGGT-1B reconstruction (up to 24 frames cleanly inside 8 GB VRAM)
        with multi-view free-space carving, Mask2Former-3D semantic understanding & dot
        realignment, optional 9-axis IMU fusion, and architectural floor/wall/ceiling completion.
        """
        t_start = time.time()
        raw = self._infer_raw(image_paths, target_w=364)
        valid_masks = self._compute_clean_masks(raw, conf_percentile=42.0)

        flip = np.array([1.0, -1.0, -1.0], dtype=np.float64)
        S = len(image_paths)

        if use_spatial_model and S > 0 and int(np.sum(valid_masks)) >= 200:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            from spatial_model import run_semantic_spatial_pipeline

            spatial_res = run_semantic_spatial_pipeline(
                raw["world_pts"].astype(np.float64) * flip,
                raw["imgs_rgb"],
                valid_masks,
                raw["cam_centers"].astype(np.float64) * flip,
                raw["cam_ups"].astype(np.float64) * flip,
                clip_ids_per_frame=clip_ids_per_frame,
                max_raw_points=min(200000, max_points),
                max_fill_points=160000,
                device=self._device,
                cam_fwds_gl=raw["cam_fwds"].astype(np.float64) * flip,
                imu_kf=imu_kf,
            )
            keyframes_out = spatial_res["keyframes"]
            if keyframes_meta:
                for s_i in range(min(len(keyframes_out), len(keyframes_meta))):
                    if "time_s" in keyframes_meta[s_i]:
                        keyframes_out[s_i]["time_s"] = keyframes_meta[s_i]["time_s"]

            elapsed = round(time.time() - t_start, 2)
            payload = {
                "points": spatial_res["points"],
                "keyframes": keyframes_out,
                "wall_segments": spatial_res["wall_segments"],
                "predicted_openings": spatial_res["predicted_openings"],
                "room_envelope": spatial_res["room_envelope"],
                "raw_point_count": spatial_res["raw_point_count"],
                "completed_point_count": spatial_res["completed_point_count"],
                "semantic_model": spatial_res.get("semantic_model"),
                "imu_meta": spatial_res.get("imu_meta", {"imu_used": False}),
                "elapsed_s": elapsed,
                "device": self._device,
                "windows": 1,
            }
            if output_json is not None:
                arr = np.asarray(payload["points"], dtype=np.float64)
                if len(arr) > 0:
                    ply_out = output_json.with_suffix(".ply")
                    out_pcd = o3d.geometry.PointCloud()
                    out_pcd.points = o3d.utility.Vector3dVector(arr[:, :3])
                    out_pcd.colors = o3d.utility.Vector3dVector(arr[:, 3:6])
                    o3d.io.write_point_cloud(str(ply_out), out_pcd)
                output_json.parent.mkdir(parents=True, exist_ok=True)
                with open(output_json, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
            print(
                f"Global VGGT+SpatialModel complete in {elapsed}s on {self._device}: "
                f"{S} frames -> {payload['raw_point_count']:,} realigned + "
                f"{payload['completed_point_count']:,} AI-filled pts, "
                f"{len(payload['wall_segments'])} walls, {len(payload['predicted_openings'])} openings"
            )
            return payload

        pts_list, cols_list = [], []
        keyframes_out: List[Dict[str, Any]] = []

        for s_i in range(S):
            m = valid_masks[s_i]
            if np.any(m):
                pts_list.append(raw["world_pts"][s_i][m] * flip)
                cols_list.append(raw["imgs_rgb"][s_i][m].astype(np.float64))
            cam_gl = raw["cam_centers"][s_i] * flip
            kf_entry: Dict[str, Any] = {
                "index": s_i + 1,
                "pose": [round(float(c), 3) for c in cam_gl],
                "yaw": round(math.degrees(math.atan2(float(cam_gl[0]), float(cam_gl[2]) + 1e-6)), 1),
                "pitch": 15.0,
                "inliers": int(np.sum(m)),
                "points": int(np.sum(m)),
                "mode": "vggt-global",
                "time_s": keyframes_meta[s_i]["time_s"] if keyframes_meta and s_i < len(keyframes_meta) else round(float(s_i) * 0.5, 2),
            }
            if clip_ids_per_frame and s_i < len(clip_ids_per_frame):
                kf_entry["clip_id"] = clip_ids_per_frame[s_i]
            keyframes_out.append(kf_entry)

        if not pts_list:
            raise RuntimeError("No valid 3D points survived multi-view filtering")

        p_all = np.vstack(pts_list)
        c_all = np.vstack(cols_list)

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(p_all)
        pcd.colors = o3d.utility.Vector3dVector(c_all)
        span_raw = max(0.3, float(np.linalg.norm(np.percentile(p_all, 90, axis=0) - np.percentile(p_all, 10, axis=0))))
        pcd = pcd.voxel_down_sample(voxel_size=max(0.002, span_raw / 420.0))
        max_pre = max(int(max_points * 1.35), 350000)
        if len(pcd.points) > max_pre:
            stride = max(2, int(len(pcd.points) // max_pre))
            pcd = pcd.select_by_index(np.arange(0, len(pcd.points), stride))
        if len(pcd.points) >= 100:
            pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=24, std_ratio=1.2)
        if len(pcd.points) >= 100:
            pcd, _ = pcd.remove_radius_outlier(nb_points=8, radius=max(0.015, 0.015 * span_raw))

        # Fast coarse-grid DBSCAN + KDTree projection removes detached glass/window reflection islands
        if len(pcd.points) >= 200:
            db_eps = max(0.035, 0.04 * span_raw)
            coarse_pcd = pcd.voxel_down_sample(voxel_size=max(0.015, 0.014 * span_raw))
            if len(coarse_pcd.points) >= 80:
                c_labels = np.array(coarse_pcd.cluster_dbscan(eps=db_eps, min_points=6))
                if np.any(c_labels >= 0):
                    uniq, cnts = np.unique(c_labels[c_labels >= 0], return_counts=True)
                    keep_lbls = set(uniq[cnts >= max(24, int(0.04 * len(c_labels)))].tolist())
                    if not keep_lbls:
                        keep_lbls = {int(uniq[int(np.argmax(cnts))])}
                    kept_coarse = np.asarray(coarse_pcd.points)[np.isin(c_labels, list(keep_lbls))]
                    if len(kept_coarse) > 0:
                        dists, _ = cKDTree(kept_coarse).query(
                            np.asarray(pcd.points), k=1, distance_upper_bound=db_eps * 1.1, workers=-1
                        )
                        keep_idx = np.where(np.isfinite(dists))[0]
                        if len(keep_idx) >= 200:
                            pcd = pcd.select_by_index(keep_idx)

        pts_np = np.asarray(pcd.points)
        cols_np = np.asarray(pcd.colors)
        cam_ups_gl = raw["cam_ups"] * flip

        R_level = np.eye(3, dtype=np.float64)
        origin_lvl = np.zeros(3, dtype=np.float64)
        norm_s_lvl = 1.0
        if len(pts_np) > 0:
            pts_np, keyframes_out, R_level, origin_lvl, norm_s_lvl = _compute_level_transform(
                pts_np, keyframes_out, target_span=target_span, cam_ups_gl=cam_ups_gl
            )
            pts_np = smooth_structural_planes(pts_np)

        if len(pts_np) > max_points:
            sel = np.linspace(0, len(pts_np) - 1, max_points, dtype=int)
            pts_np = pts_np[sel]
            cols_np = cols_np[sel]

        pts_np, cols_np, room_env = (
            enforce_room_envelope(pts_np, cols_np, keyframes_out)
            if len(pts_np) > 0
            else (pts_np, cols_np, {"wall_segments": [], "predicted_openings": [], "room_polygon": [], "floor_y": 0.0, "roof_y": 1.0})
        )
        wall_segments = room_env["wall_segments"]
        predicted_openings = room_env["predicted_openings"]
        room_envelope = {
            "room_polygon": room_env["room_polygon"],
            "floor_y": room_env["floor_y"],
            "roof_y": room_env["roof_y"],
        }

        if anchor_dir is not None:
            kf_save_dir = anchor_dir / "keyframes"
            kf_save_dir.mkdir(parents=True, exist_ok=True)
            anchor_pts_list, anchor_masks_list, saved_kf_paths = [], [], []
            for s_i, p_str in enumerate(image_paths):
                src_jpg = Path(p_str)
                dst_jpg = kf_save_dir / src_jpg.name
                shutil.copy2(src_jpg, dst_jpg)
                saved_kf_paths.append(str(dst_jpg))
                pts_gl_full = raw["world_pts"][s_i] * flip
                pts_sub = pts_gl_full[::2, ::2]
                mask_sub = valid_masks[s_i][::2, ::2]
                H_s, W_s, _ = pts_sub.shape
                pts_flat = ((pts_sub.reshape(-1, 3) @ R_level.T) - origin_lvl) * norm_s_lvl
                pts_flat[:, 1] += 0.01
                anchor_pts_list.append(pts_flat.reshape(H_s, W_s, 3).astype(np.float32))
                anchor_masks_list.append(mask_sub.astype(bool))
            np.savez_compressed(
                anchor_dir / "frames_3d.npz",
                pts=np.stack(anchor_pts_list, axis=0),
                masks=np.stack(anchor_masks_list, axis=0),
                paths=np.array(saved_kf_paths),
            )

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

        elapsed = round(time.time() - t_start, 2)
        payload = {
            "points": points_list,
            "keyframes": keyframes_out,
            "wall_segments": wall_segments,
            "predicted_openings": predicted_openings,
            "room_envelope": room_envelope,
            "elapsed_s": elapsed,
            "device": self._device,
            "windows": 1,
        }

        if output_json is not None:
            ply_out = output_json.with_suffix(".ply")
            out_pcd = o3d.geometry.PointCloud()
            out_pcd.points = o3d.utility.Vector3dVector(pts_np)
            out_pcd.colors = o3d.utility.Vector3dVector(cols_np)
            o3d.io.write_point_cloud(str(ply_out), out_pcd)
            output_json.parent.mkdir(parents=True, exist_ok=True)
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(payload, f)

        print(
            f"Global VGGT-1B complete in {elapsed}s on {self._device}: "
            f"{S} sharp frames -> {len(points_list):,} cleaned 3D points, "
            f"{len(wall_segments)} walls, {len(predicted_openings)} openings"
        )
        return payload

    def reconstruct_video_incremental(
        self,
        video_path: str,
        output_json: Path,
        window_size: int = 8,
        overlap: int = 3,
        max_keyframes: int = 32,
        max_points: int = 250000,
        conf_percentile: float = 48.0,
        progress_cb: Optional[Callable[[str, List[List[float]], List[Dict[str, Any]]], None]] = None,
        anchor_dir: Optional[Path] = None,
        imu_session: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Incrementally reconstructs a walk-around video in VRAM-safe overlapping windows,
        refining each window with RANSAC Sim(3) + Open3D ICP and optional synced IMU fusion.
        """
        t_start = time.time()
        flip_cv_gl = np.diag([1.0, -1.0, -1.0, 1.0])

        imu_series: Optional[Dict[str, np.ndarray]] = None
        imu_offset_s: float = 0.0
        if imu_session:
            from imu_fusion import align_video_to_imu

            imu_series, imu_offset_s = align_video_to_imu(video_path, imu_session)

        with tempfile.TemporaryDirectory(prefix="vggt_walk_") as tmp:
            kf_dir = Path(tmp) / "keyframes"
            kf_meta = extract_sharp_keyframes(
                video_path,
                kf_dir,
                target_segments=max_keyframes + 8,
                max_keyframes=max_keyframes,
                imu=imu_series,
                imu_offset_s=imu_offset_s,
            )
            if len(kf_meta) < 2:
                raise RuntimeError("Fewer than 2 sharp keyframes extracted from video")

            imu_kf: Optional[List[Optional[Dict[str, Any]]]] = None
            if imu_series is not None:
                from imu_fusion import interpolate_imu_at_times

                imu_kf = interpolate_imu_at_times(
                    imu_series, [float(k["time_s"]) for k in kf_meta], time_offset_s=imu_offset_s
                )

            paths = [k["path"] for k in kf_meta]
            n_total = len(paths)
            if n_total <= 24:
                payload = self.reconstruct_frames_global(
                    paths,
                    output_json=output_json,
                    keyframes_meta=kf_meta,
                    max_points=max_points,
                    anchor_dir=anchor_dir,
                    imu_kf=imu_kf,
                )
                if imu_kf is not None:
                    payload["imu_kf"] = imu_kf
                    if isinstance(payload.get("imu_meta"), dict):
                        payload["imu_meta"]["session_id"] = imu_session.get("session_id")
                        payload["imu_meta"]["sync_offset_s"] = imu_offset_s
                if progress_cb and payload.get("points"):
                    progress_cb("Global VGGT pass complete", payload["points"], payload["keyframes"])
                return payload

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
            base_voxel: Optional[float] = None
            # Map kf_index -> {"world_pts": (H, W, 3), "mask": (H, W), "cam_pos": (3,)}
            resolved_frames: Dict[int, Dict[str, Any]] = {}
            keyframes_out: List[Dict[str, Any]] = []

            for w_i, win_idx in enumerate(windows):
                t_w = time.time()
                win_paths = [paths[k] for k in win_idx]
                res = self._infer_window(win_paths, conf_percentile=conf_percentile)

                # Determine RANSAC Sim(3) transform from this window to Global World Space
                if w_i == 0:
                    scale, R_align, t_align = 1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64)
                else:
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
                                src_samples.append(cur_pts[both][::4])
                                dst_samples.append(prev_pts[both][::4])
                    if src_samples:
                        src_cat = np.concatenate(src_samples, axis=0).astype(np.float64)
                        dst_cat = np.concatenate(dst_samples, axis=0).astype(np.float64)
                        if len(src_cat) > 3000:
                            step_s = max(1, len(src_cat) // 3000)
                            src_cat = src_cat[::step_s]
                            dst_cat = dst_cat[::step_s]
                        scale, R_align, t_align = _umeyama_sim3_ransac(src_cat, dst_cat)
                    else:
                        scale, R_align, t_align = 1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64)

                # Transform window frames into global OpenCV coords & build Open3D window cloud (Three.js space)
                win_pts_gl: List[np.ndarray] = []
                win_cols_gl: List[np.ndarray] = []
                frame_cache: List[Tuple[int, np.ndarray, np.ndarray, np.ndarray, bool]] = []

                for local_i, kf_i in enumerate(win_idx):
                    pts_loc = res["world_pts"][local_i].astype(np.float64)
                    H, W, _ = pts_loc.shape
                    pts_aligned = (scale * (pts_loc.reshape(-1, 3) @ R_align.T) + t_align).reshape(H, W, 3)
                    cam_aligned = scale * (R_align @ res["cam_centers"][local_i]) + t_align
                    mask = res["valid_masks"][local_i]
                    is_new_frame = kf_i not in resolved_frames
                    frame_cache.append((kf_i, pts_aligned, mask, cam_aligned, is_new_frame))

                    if np.any(mask):
                        pts_gl = pts_aligned[mask] * np.array([1.0, -1.0, -1.0], dtype=np.float64)
                        cols_gl = res["imgs_rgb"][local_i][mask].astype(np.float64)
                        win_pts_gl.append(pts_gl)
                        win_cols_gl.append(cols_gl)

                # Geometric Point-to-Plane ICP refinement locking overlapping walls/furniture onto global_pcd
                T_icp_gl = np.eye(4, dtype=np.float64)
                if win_pts_gl:
                    raw_win_pts = np.concatenate(win_pts_gl, axis=0)
                    raw_win_cols = np.concatenate(win_cols_gl, axis=0)
                    if base_voxel is None:
                        w0_span = max(0.2, float(np.linalg.norm(np.percentile(raw_win_pts, 90, axis=0) - np.percentile(raw_win_pts, 10, axis=0))))
                        base_voxel = max(0.0005, w0_span / 380.0)

                    win_pcd = o3d.geometry.PointCloud()
                    win_pcd.points = o3d.utility.Vector3dVector(raw_win_pts)
                    win_pcd.colors = o3d.utility.Vector3dVector(raw_win_cols)
                    win_pcd = win_pcd.voxel_down_sample(voxel_size=base_voxel)
                    if w_i > 0 and len(global_pcd.points) >= 500:
                        icp_dist = base_voxel * 6.0
                        win_pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=base_voxel * 4.0, max_nn=24))
                        global_pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=base_voxel * 4.0, max_nn=24))
                        reg = o3d.pipelines.registration.registration_icp(
                            win_pcd,
                            global_pcd,
                            max_correspondence_distance=icp_dist,
                            init=np.eye(4),
                            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                            criteria=o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=30),
                        )
                        if reg.fitness > 0.25:
                            T_icp_gl = reg.transformation
                            win_pcd.transform(T_icp_gl)
                    global_pcd = global_pcd + win_pcd
                    global_pcd = global_pcd.voxel_down_sample(voxel_size=base_voxel)

                T_icp_cv = flip_cv_gl @ T_icp_gl @ flip_cv_gl
                R_icp, t_icp = T_icp_cv[:3, :3], T_icp_cv[:3, 3]
                for kf_i, pts_aligned, mask, cam_aligned, is_new_frame in frame_cache:
                    H, W, _ = pts_aligned.shape
                    pts_final = (pts_aligned.reshape(-1, 3) @ R_icp.T + t_icp).reshape(H, W, 3)
                    cam_final = R_icp @ cam_aligned + t_icp
                    resolved_frames[kf_i] = {
                        "world_pts": pts_final,
                        "mask": mask,
                        "cam_pos": cam_final,
                    }
                    if is_new_frame:
                        cam_gl = cam_final * np.array([1.0, -1.0, -1.0], dtype=np.float64)
                        keyframes_out.append({
                            "index": kf_i + 1,
                            "pose": [round(float(c), 3) for c in cam_gl],
                            "yaw": round(math.degrees(math.atan2(float(cam_gl[0]), float(cam_gl[2]) + 1e-6)), 1),
                            "pitch": 15.0,
                            "inliers": int(np.sum(mask)),
                            "points": int(np.sum(mask)),
                            "mode": "vggt-walkthrough",
                            "time_s": kf_meta[kf_i]["time_s"],
                        })

                msg = f"Window {w_i+1}/{len(windows)} done in {time.time()-t_w:.2f}s -> {len(global_pcd.points):,} pts"
                print(msg)
                if progress_cb and len(global_pcd.points) >= 50:
                    prev_pts, prev_kfs = auto_level_scene(np.asarray(global_pcd.points), keyframes_out)
                    prev_cols = np.asarray(global_pcd.colors)
                    if len(prev_pts) > max_points:
                        sel = np.linspace(0, len(prev_pts) - 1, max_points, dtype=int)
                        prev_pts, prev_cols = prev_pts[sel], prev_cols[sel]
                    prev_list = [
                        [
                            round(float(prev_pts[i, 0]), 4),
                            round(float(prev_pts[i, 1]), 4),
                            round(float(prev_pts[i, 2]), 4),
                            round(float(prev_cols[i, 0]), 3),
                            round(float(prev_cols[i, 1]), 3),
                            round(float(prev_cols[i, 2]), 3),
                        ]
                        for i in range(len(prev_pts))
                    ]
                    progress_cb(msg, prev_list, prev_kfs)

            # Final statistical + radius outlier denoising & auto-leveling
            if len(global_pcd.points) >= 100:
                global_pcd, _ = global_pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=1.5)
            if len(global_pcd.points) >= 100:
                p_tmp = np.asarray(global_pcd.points)
                span_tmp = max(0.3, float(np.linalg.norm(np.percentile(p_tmp, 90, axis=0) - np.percentile(p_tmp, 10, axis=0))))
                global_pcd, _ = global_pcd.remove_radius_outlier(nb_points=6, radius=max(0.015, 0.018 * span_tmp))

            pts_np = np.asarray(global_pcd.points)
            cols_np = np.asarray(global_pcd.colors)

            R_level = np.eye(3, dtype=np.float64)
            origin_lvl = np.zeros(3, dtype=np.float64)
            norm_s_lvl = 1.0
            if len(pts_np) > 0:
                pts_np, keyframes_out, R_level, origin_lvl, norm_s_lvl = _compute_level_transform(pts_np, keyframes_out)
                pts_np = smooth_structural_planes(pts_np)

            # Adaptive room-detail cap (default 250,000 points for crisp furniture/edges)
            if len(pts_np) > max_points:
                sel = np.linspace(0, len(pts_np) - 1, max_points, dtype=int)
                pts_np = pts_np[sel]
                cols_np = cols_np[sel]

            pts_np, cols_np, room_env = (
                enforce_room_envelope(pts_np, cols_np, keyframes_out)
                if len(pts_np) > 0
                else (pts_np, cols_np, {"wall_segments": [], "predicted_openings": [], "room_polygon": [], "floor_y": 0.0, "roof_y": 1.0})
            )
            wall_segments = room_env["wall_segments"]
            predicted_openings = room_env["predicted_openings"]
            room_envelope = {
                "room_polygon": room_env["room_polygon"],
                "floor_y": room_env["floor_y"],
                "roof_y": room_env["roof_y"],
            }

            if anchor_dir is not None and resolved_frames:
                kf_save_dir = anchor_dir / "keyframes"
                kf_save_dir.mkdir(parents=True, exist_ok=True)
                anchor_pts_list = []
                anchor_masks_list = []
                saved_kf_paths = []
                for kf_i in sorted(resolved_frames.keys()):
                    src_jpg = Path(kf_meta[kf_i]["path"])
                    dst_jpg = kf_save_dir / src_jpg.name
                    shutil.copy2(src_jpg, dst_jpg)
                    saved_kf_paths.append(str(dst_jpg))
                    rf = resolved_frames[kf_i]
                    pts_gl_full = rf["world_pts"] * np.array([1.0, -1.0, -1.0], dtype=np.float64)
                    pts_sub = pts_gl_full[::2, ::2]
                    mask_sub = rf["mask"][::2, ::2]
                    H_s, W_s, _ = pts_sub.shape
                    pts_flat = ((pts_sub.reshape(-1, 3) @ R_level.T) - origin_lvl) * norm_s_lvl
                    pts_flat[:, 1] += 0.01
                    anchor_pts_list.append(pts_flat.reshape(H_s, W_s, 3).astype(np.float32))
                    anchor_masks_list.append(mask_sub.astype(bool))
                np.savez_compressed(
                    anchor_dir / "frames_3d.npz",
                    pts=np.stack(anchor_pts_list, axis=0),
                    masks=np.stack(anchor_masks_list, axis=0),
                    paths=np.array(saved_kf_paths),
                )

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
                "wall_segments": wall_segments,
                "predicted_openings": predicted_openings,
                "room_envelope": room_envelope,
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
    recon.reconstruct_video_incremental(vid, out_file, window_size=8, overlap=3, max_keyframes=32, max_points=250000)
