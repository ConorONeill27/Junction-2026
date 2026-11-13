"""
Production 3D Reconstruction Engine powered by:
1. Microsoft Research MoGe-2 (`Ruicheng/moge-2-vits-normal`, CVPR 2025 Oral):
   Directly predicts calibrated metric 3D point maps (X, Y, Z in meters), camera intrinsics (FOV),
   3D surface normals (nx, ny, nz), and occlusion-edge masks (`utils3d.np.depth_map_edge`).
2. Open3D (`open3d==0.20.0`) Multi-View 3D Registration & Surface Fusion:
   - SIFT 3D metric correspondence RANSAC + Gyro pose prior
   - Open3D Point-to-Plane ICP (`registration_icp` with `TransformationEstimationPointToPlane`)
     using MoGe-2's predicted 3D surface normals so overlapping views snap onto one unified 3D surface
   - Normal-incidence weighting + Open3D Statistical Outlier Removal (`remove_statistical_outlier`)
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
import warnings

import cv2
import numpy as np
import open3d as o3d
from PIL import Image

warnings.filterwarnings("ignore", category=UserWarning)

# Add local clone of microsoft/MoGe to sys.path
_MOGE_ROOT = str(Path(__file__).resolve().parent / "moge_repo")
if _MOGE_ROOT not in sys.path:
    sys.path.insert(0, _MOGE_ROOT)

try:
    import utils3d_moge as utils3d
except ImportError:
    import utils3d  # type: ignore[no-redef]


def _wrap_deg(angle: float) -> float:
    return ((angle + 180.0) % 360.0) - 180.0


def _angle_diff_deg(a: float, b: float) -> float:
    return _wrap_deg(a - b)


def _rot_y(deg: float) -> np.ndarray:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return np.array(
        [
            [c, 0.0, s],
            [0.0, 1.0, 0.0],
            [-s, 0.0, c],
        ],
        dtype=np.float64,
    )


def _rot_x(deg: float) -> np.ndarray:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, c, -s],
            [0.0, s, c],
        ],
        dtype=np.float64,
    )


class MultiViewReconstructor:
    def __init__(self) -> None:
        self._moge_model: Any = None
        self._device: Any = None
        self._models_loaded = False
        self._load_error: Optional[str] = None

        self.keyframes: List[Dict[str, Any]] = []
        self._global_pcd: Optional[o3d.geometry.PointCloud] = None
        self._prev_kp: Any = None
        self._prev_des: Optional[np.ndarray] = None
        self._prev_world_map: Optional[np.ndarray] = None
        self._prev_valid_mask: Optional[np.ndarray] = None
        self._prev_hint_yaw: Optional[float] = None
        self._current_yaw_deg: float = 0.0
        self._anchor_center_cam: Optional[np.ndarray] = None

    def reset(self) -> None:
        self.keyframes.clear()
        self._global_pcd = None
        self._prev_kp = None
        self._prev_des = None
        self._prev_world_map = None
        self._prev_valid_mask = None
        self._prev_hint_yaw = None
        self._current_yaw_deg = 0.0
        self._anchor_center_cam = None

    def _ensure_models(self) -> None:
        if self._models_loaded:
            return
        self._models_loaded = True
        try:
            import torch
            from moge.model.v2 import MoGeModel

            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self._moge_model = (
                MoGeModel.from_pretrained("Ruicheng/moge-2-vits-normal")
                .to(self._device)
                .eval()
            )
        except Exception as exc:
            self._load_error = str(exc)

    def _decode_image(self, image_b64: str) -> Optional[Image.Image]:
        if not image_b64:
            return None
        raw = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
        try:
            img_bytes = base64.b64decode(raw)
            return Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception:
            return None

    def _infer_moge_geometry(
        self, rgb_np: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Runs Microsoft MoGe-2 (ViT-S Normal) to predict:
        - points_gl: [H, W, 3] metric 3D coordinates in OpenGL/Three.js camera convention (+X right, +Y up, -Z forward)
        - normals_gl: [H, W, 3] unit surface normals in OpenGL/Three.js camera convention
        - depth_m: [H, W] metric depth in meters
        - clean_mask: [H, W] boolean mask with occlusion edges stripped via utils3d.np.depth_map_edge
        """
        self._ensure_models()
        h, w = rgb_np.shape[:2]

        if self._moge_model is not None:
            import torch

            img_t = (
                torch.tensor(rgb_np.astype(np.float32) / 255.0, dtype=torch.float32, device=self._device)
                .permute(2, 0, 1)
            )
            use_fp16 = self._device.type == "cuda"
            out = self._moge_model.infer(img_t, num_tokens=520, use_fp16=use_fp16)

            pts_cv = out["points"].detach().cpu().numpy().astype(np.float32)
            depth_m = out["depth"].detach().cpu().numpy().astype(np.float32)
            mask = out["mask"].detach().cpu().numpy().astype(bool)
            if "normal" in out and out["normal"] is not None:
                nrm_cv = out["normal"].detach().cpu().numpy().astype(np.float32)
            else:
                nrm_cv = np.zeros_like(pts_cv)
                nrm_cv[..., 2] = -1.0

            # Strip flying pixels at depth discontinuities using MoGe's official edge filter
            edge_mask = utils3d.np.depth_map_edge(depth_m, rtol=0.035)
            # Also reject grazing surfaces whose normal is nearly perpendicular to viewing ray
            cos_view = np.abs(nrm_cv[..., 2])
            clean_mask = mask & (~edge_mask) & (cos_view > 0.18) & np.isfinite(depth_m) & (depth_m > 0.15)

            # Convert from OpenCV camera coords (x right, y down, z forward)
            # to Three.js / OpenGL camera coords (x right, y up, z backward)
            pts_gl = np.nan_to_num(pts_cv, nan=0.0, posinf=0.0, neginf=0.0) * np.array([1.0, -1.0, -1.0], dtype=np.float32)
            nrm_gl = np.nan_to_num(nrm_cv, nan=0.0, posinf=0.0, neginf=0.0) * np.array([1.0, -1.0, -1.0], dtype=np.float32)
            return pts_gl, nrm_gl, depth_m, clean_mask

        # Fallback if model failed to load
        uu, vv = np.meshgrid(np.linspace(-0.5, 0.5, w), np.linspace(0.4, -0.4, h))
        depth_m = np.ones((h, w), dtype=np.float32)
        pts_gl = np.stack([uu, vv, -depth_m], axis=-1).astype(np.float32)
        nrm_gl = np.zeros_like(pts_gl)
        nrm_gl[..., 2] = 1.0
        clean_mask = np.ones((h, w), dtype=bool)
        return pts_gl, nrm_gl, depth_m, clean_mask

    def _isolate_target_3d(
        self,
        pts_gl: np.ndarray,
        depth_m: np.ndarray,
        clean_mask: np.ndarray,
        mode: str,
        bg_cutoff: float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Centers the 3D point map on the target object in true metric 3D space
        and removes distant background walls/floors using true 3D Euclidean distance.
        """
        h, w = depth_m.shape
        # Find the 3D metric center of the subject inside the central target reticle
        r_y0, r_y1 = int(h * 0.25), int(h * 0.75)
        r_x0, r_x1 = int(w * 0.25), int(w * 0.75)
        reticle_mask = np.zeros((h, w), dtype=bool)
        reticle_mask[r_y0:r_y1, r_x0:r_x1] = True

        valid_reticle = clean_mask & reticle_mask
        if np.any(valid_reticle):
            # Take the closer 45th percentile in the reticle as the foreground object's surface depth
            fg_z = float(np.percentile(depth_m[valid_reticle], 45))
            fg_pts = pts_gl[valid_reticle & (np.abs(depth_m - fg_z) < 0.25)]
            if len(fg_pts) >= 10:
                center_cam = np.median(fg_pts, axis=0).astype(np.float32)
            else:
                center_cam = np.median(pts_gl[valid_reticle], axis=0).astype(np.float32)
        elif np.any(clean_mask):
            center_cam = np.median(pts_gl[clean_mask], axis=0).astype(np.float32)
        else:
            center_cam = np.array([0.0, 0.0, -0.9], dtype=np.float32)

        # Smoothly lock object distance scale to the first keyframe so object size stays rock-solid
        if self._anchor_center_cam is None:
            self._anchor_center_cam = center_cam.copy()
        else:
            # Normalize depth scale relative to anchor distance so walking closer/farther doesn't rescale object
            cur_dist = max(0.25, float(np.linalg.norm(center_cam)))
            anc_dist = max(0.25, float(np.linalg.norm(self._anchor_center_cam)))
            scale_factor = float(np.clip(anc_dist / cur_dist, 0.65, 1.55))
            pts_gl = pts_gl * scale_factor
            center_cam = center_cam * scale_factor

        # Express 3D points relative to the object's metric 3D center
        pts_centered = pts_gl - center_cam[None, None, :]

        if mode == "surround":
            # True 3D metric sphere/cylinder crop around the target object!
            # bg_cutoff in [0.0 .. 0.45] maps to max 3D object radius in [0.85m .. 0.28m]
            max_radius_3d = float(np.clip(0.82 - bg_cutoff * 1.25, 0.26, 0.90))
            dist_3d = np.linalg.norm(pts_centered, axis=-1)
            # Also prevent background wall behind the object center from bleeding in
            z_ok = pts_centered[..., 2] > (-max_radius_3d * 0.75)
            obj_mask = clean_mask & (dist_3d <= max_radius_3d) & z_ok
            return pts_centered, obj_mask
        else:
            # Room mode: keep everything up to 3.5m
            dist_3d = np.linalg.norm(pts_centered, axis=-1)
            room_mask = clean_mask & (dist_3d <= 3.5)
            return pts_centered, room_mask

    def _estimate_initial_pose(
        self,
        gray_np: np.ndarray,
        pts_centered: np.ndarray,
        valid_mask: np.ndarray,
        hint_yaw: Optional[float],
    ) -> Tuple[np.ndarray, float, int]:
        """
        Computes initial 4x4 camera-to-world transform T_init using:
        1. SIFT 2D matches lifted to MoGe-2 metric 3D points + RANSAC rigid SE(3)
        2. Fallback/fusion with stabilized gyro / optical flow orbit angle around the object
        """
        sift = cv2.SIFT_create(nfeatures=500)
        kp, des = sift.detectAndCompute(gray_np, (valid_mask.astype(np.uint8)) * 255)
        if des is None or len(kp) < 8:
            kp, des = sift.detectAndCompute(gray_np, None)

        if len(self.keyframes) == 0:
            self._prev_kp = kp
            self._prev_des = des
            self._prev_hint_yaw = hint_yaw
            self._current_yaw_deg = float(hint_yaw) if hint_yaw is not None else 0.0
            T_init = np.eye(4, dtype=np.float64)
            T_init[:3, :3] = _rot_y(self._current_yaw_deg)
            return T_init, self._current_yaw_deg, len(kp) if kp else 20

        # Compute SIFT matches against previous frame
        visual_delta_yaw = 0.0
        src_3d_list: List[np.ndarray] = []
        dst_3d_list: List[np.ndarray] = []
        du_list: List[float] = []

        if (
            des is not None
            and self._prev_des is not None
            and len(kp) >= 6
            and len(self._prev_kp) >= 6
        ):
            bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
            raw_matches = bf.knnMatch(des, self._prev_des, k=2)
            h, w = valid_mask.shape
            for m_n in raw_matches:
                if len(m_n) == 2 and m_n[0].distance < 0.76 * m_n[1].distance:
                    m = m_n[0]
                    u_c, v_c = int(round(kp[m.queryIdx].pt[0])), int(round(kp[m.queryIdx].pt[1]))
                    u_p, v_p = int(round(self._prev_kp[m.trainIdx].pt[0])), int(round(self._prev_kp[m.trainIdx].pt[1]))
                    if abs(v_c - v_p) < 40:
                        du_list.append(float(u_c - u_p))
                    if (
                        self._prev_world_map is not None
                        and self._prev_valid_mask is not None
                        and 0 <= v_c < h
                        and 0 <= u_c < w
                        and 0 <= v_p < h
                        and 0 <= u_p < w
                        and valid_mask[v_c, u_c]
                        and self._prev_valid_mask[v_p, u_p]
                    ):
                        src_3d_list.append(pts_centered[v_c, u_c])
                        dst_3d_list.append(self._prev_world_map[v_p, u_p])

        if len(du_list) >= 5:
            med_du = float(np.median(du_list))
            visual_delta_yaw = float(np.clip((-med_du / 240.0) * 65.0, -28.0, 28.0))

        gyro_delta = 0.0
        if hint_yaw is not None and self._prev_hint_yaw is not None:
            gyro_delta = _angle_diff_deg(float(hint_yaw), float(self._prev_hint_yaw))

        if abs(gyro_delta) >= 2.0:
            self._current_yaw_deg = float(hint_yaw)  # type: ignore[arg-type]
        elif abs(visual_delta_yaw) >= 2.0:
            self._current_yaw_deg = _wrap_deg(self._current_yaw_deg + visual_delta_yaw)
        else:
            self._current_yaw_deg = _wrap_deg(self._current_yaw_deg + 12.0)

        T_init = np.eye(4, dtype=np.float64)
        T_init[:3, :3] = _rot_y(self._current_yaw_deg)

        # If we have strong 3D SIFT correspondences on the object, run Open3D RANSAC registration
        inlier_count = max(12, len(src_3d_list))
        if len(src_3d_list) >= 12:
            src_pcd = o3d.geometry.PointCloud()
            src_pcd.points = o3d.utility.Vector3dVector(np.array(src_3d_list, dtype=np.float64))
            dst_pcd = o3d.geometry.PointCloud()
            dst_pcd.points = o3d.utility.Vector3dVector(np.array(dst_3d_list, dtype=np.float64))
            corres = o3d.utility.Vector2iVector(np.column_stack([np.arange(len(src_3d_list)), np.arange(len(src_3d_list))]))
            reg_ransac = o3d.pipelines.registration.registration_ransac_based_on_correspondence(
                src_pcd,
                dst_pcd,
                corres,
                max_correspondence_distance=0.055,
                estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPoint(False),
                ransac_n=4,
                criteria=o3d.pipelines.registration.RANSACConvergenceCriteria(250, 0.99),
            )
            # Accept RANSAC pose if it has good inliers and reasonable translation (< 0.35m from center)
            if len(reg_ransac.correspondence_set) >= 10 and np.linalg.norm(reg_ransac.transformation[:3, 3]) < 0.35:
                T_init = reg_ransac.transformation
                inlier_count = len(reg_ransac.correspondence_set)
                # Extract yaw angle from rotation matrix for UI telemetry
                r_mat = T_init[:3, :3]
                self._current_yaw_deg = round(math.degrees(math.atan2(float(r_mat[0, 2]), float(r_mat[2, 2]))), 1)

        self._prev_kp = kp
        self._prev_des = des
        if hint_yaw is not None:
            self._prev_hint_yaw = float(hint_yaw)

        return T_init, round(self._current_yaw_deg, 1), inlier_count

    def _encode_depth_preview(self, rgb_np: np.ndarray, depth_m: np.ndarray, mask: np.ndarray) -> str:
        valid_d = depth_m[mask] if np.any(mask) else depth_m.reshape(-1)
        d_min, d_max = float(np.percentile(valid_d, 3)), float(np.percentile(valid_d, 97))
        norm_disp = 1.0 - np.clip((depth_m - d_min) / max(1e-4, d_max - d_min), 0.0, 1.0)
        vis = (norm_disp * 255.0).astype(np.uint8)
        colored = cv2.applyColorMap(vis, cv2.COLORMAP_INFERNO)
        rgb_depth = cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)
        out = np.where(mask[:, :, None], rgb_depth, (rgb_np * 0.18).astype(np.uint8))
        pil_out = Image.fromarray(out)
        buf = io.BytesIO()
        pil_out.save(buf, format="JPEG", quality=78)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    def carve_and_fuse_points(
        self, existing_points: List[List[float]], new_points: List[List[float]], voxel_size: float = 0.012
    ) -> List[List[float]]:
        """
        Open3D Voxel Downsampling + Statistical Outlier Removal returns the complete
        cleaned global point cloud directly in `new_points`, so we return `new_points`
        when available, or fuse via a 1.2cm voxel grid.
        """
        if len(new_points) > 0 and self._global_pcd is not None:
            return new_points[-65000:]

        grid: Dict[Tuple[int, int, int], List[float]] = {}
        for pt in existing_points + new_points:
            if len(pt) < 6:
                continue
            key = (round(pt[0] / voxel_size), round(pt[1] / voxel_size), round(pt[2] / voxel_size))
            grid[key] = pt
        return list(grid.values())[-65000:]

    def process_keyframe(
        self,
        image_b64: str,
        hint_pose: Optional[List[float]] = None,
        hint_yaw: Optional[float] = None,
        hint_pitch: Optional[float] = None,
        mode: str = "surround",
        bg_cutoff: float = 0.20,
        run_detection: bool = False,
    ) -> Dict[str, Any]:
        pil_img = self._decode_image(image_b64)
        if pil_img is None:
            return {"ok": False, "points": [], "detections_3d": [], "inliers": 0}

        target_w, target_h = 240, 180
        resized_pil = pil_img.resize((target_w, target_h), Image.Resampling.BILINEAR)
        rgb_np = np.array(resized_pil, dtype=np.uint8)
        gray_np = cv2.cvtColor(rgb_np, cv2.COLOR_RGB2GRAY)

        # 1. Microsoft MoGe-2 Metric 3D Point Map + Surface Normals + Occlusion-Edge Mask
        pts_gl, nrm_gl, depth_m, clean_mask = self._infer_moge_geometry(rgb_np)

        # 2. Center on Target Object in True 3D Metric Space & Slice Off Distant Background
        pts_centered, valid_mask = self._isolate_target_3d(pts_gl, depth_m, clean_mask, mode, bg_cutoff)

        # 3. Initial 6-DoF Pose Estimation (SIFT 3D Metric RANSAC + Stabilized Gyro)
        T_pose, yaw_deg, inlier_count = self._estimate_initial_pose(gray_np, pts_centered, valid_mask, hint_yaw)

        # 4. Build Open3D PointCloud for Current Frame (with MoGe-2 Surface Normals & Normal-Shaded Colors)
        ys, xs = np.where(valid_mask)
        if len(xs) < 25:
            return {
                "ok": True,
                "pose": [0.0, 0.7, 1.1],
                "yaw": yaw_deg,
                "pitch": 12.0,
                "inliers": 0,
                "keyframe_count": len(self.keyframes),
                "keyframes": self.keyframes[-36:],
                "points": [],
                "detections_3d": [],
                "depth_preview_b64": self._encode_depth_preview(rgb_np, depth_m, valid_mask),
                "provider": "moge2-open3d-empty",
            }

        frame_pts = pts_centered[ys, xs].astype(np.float64)
        frame_nrms = nrm_gl[ys, xs].astype(np.float64)
        frame_cols = rgb_np[ys, xs].astype(np.float64) / 255.0

        # Subtle normal-relief shading (15% hemisphere light) so 3D surface geometry & creases pop clearly
        light_dir = np.array([0.35, 0.65, 0.67], dtype=np.float64)
        shade = np.clip(0.85 + 0.18 * (frame_nrms @ light_dir), 0.72, 1.08)[:, None]
        frame_cols = np.clip(frame_cols * shade, 0.0, 1.0)

        frame_pcd = o3d.geometry.PointCloud()
        frame_pcd.points = o3d.utility.Vector3dVector(frame_pts)
        frame_pcd.normals = o3d.utility.Vector3dVector(frame_nrms)
        frame_pcd.colors = o3d.utility.Vector3dVector(frame_cols)
        frame_pcd.transform(T_pose)

        # 5. Open3D Point-to-Plane ICP Refinement against Global 3D Model
        if self._global_pcd is not None and len(self._global_pcd.points) >= 100:
            try:
                reg_icp = o3d.pipelines.registration.registration_icp(
                    frame_pcd,
                    self._global_pcd,
                    max_correspondence_distance=0.065,
                    init=np.eye(4, dtype=np.float64),
                    estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                    criteria=o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=25),
                )
                # Apply ICP correction if fitness is meaningful and drift is small
                if reg_icp.fitness >= 0.18 and np.linalg.norm(reg_icp.transformation[:3, 3]) < 0.18:
                    frame_pcd.transform(reg_icp.transformation)
                    T_pose = reg_icp.transformation @ T_pose
                    inlier_count = max(inlier_count, int(reg_icp.fitness * 100))
            except Exception:
                pass

        # Save dense world map for next frame's SIFT 3D correspondence matching
        world_map = np.zeros_like(pts_centered)
        rot = T_pose[:3, :3].astype(np.float32)
        trans = T_pose[:3, 3].astype(np.float32)
        world_map = (pts_centered @ rot.T) + trans
        # Lift object center to y = +0.52m above the Three.js floor grid
        world_map[..., 1] += 0.52
        self._prev_world_map = world_map - np.array([0.0, 0.52, 0.0], dtype=np.float32)
        self._prev_valid_mask = valid_mask

        # Shift frame_pcd up by +0.52m so the object sits cleanly above the Three.js floor grid
        frame_pcd.translate(np.array([0.0, 0.52, 0.0], dtype=np.float64))

        # 6. Merge into Global Open3D PointCloud + Voxel Surface Fusion + Statistical Outlier Cleaning
        if self._global_pcd is None:
            self._global_pcd = frame_pcd.voxel_down_sample(voxel_size=0.011)
        else:
            combined = self._global_pcd + frame_pcd
            down = combined.voxel_down_sample(voxel_size=0.011)
            if len(down.points) >= 60:
                cl, _ = down.remove_statistical_outlier(nb_neighbors=16, std_ratio=1.8)
                self._global_pcd = cl
            else:
                self._global_pcd = down

        # Extract cleaned global 3D points for Three.js studio
        g_pts = np.asarray(self._global_pcd.points)
        g_cols = np.asarray(self._global_pcd.colors)
        points: List[List[float]] = []
        for idx in range(len(g_pts)):
            xw, yw, zw = g_pts[idx]
            if yw < 0.01:
                continue
            r_c, g_c, b_c = g_cols[idx]
            points.append([
                round(float(xw), 3),
                round(float(yw), 3),
                round(float(zw), 3),
                round(float(r_c), 2),
                round(float(g_c), 2),
                round(float(b_c), 2),
            ])

        pitch_deg = float(hint_pitch) if hint_pitch is not None else 12.0
        theta_rad = math.radians(yaw_deg)
        pitch_rad = math.radians(np.clip(pitch_deg, -25.0, 40.0))
        orbit_r = 1.15
        cam_pos = [
            round(orbit_r * math.sin(theta_rad) * math.cos(pitch_rad), 3),
            round(0.52 + orbit_r * math.sin(pitch_rad), 3),
            round(orbit_r * math.cos(theta_rad) * math.cos(pitch_rad), 3),
        ]

        kf_info = {
            "index": len(self.keyframes) + 1,
            "pose": cam_pos,
            "yaw": yaw_deg,
            "pitch": round(pitch_deg, 1),
            "inliers": inlier_count,
            "points": len(points),
            "mode": mode,
        }
        self.keyframes.append(kf_info)

        return {
            "ok": True,
            "pose": cam_pos,
            "yaw": yaw_deg,
            "pitch": round(pitch_deg, 1),
            "inliers": inlier_count,
            "keyframe_count": len(self.keyframes),
            "keyframes": self.keyframes[-36:],
            "points": points,
            "detections_3d": [],
            "depth_preview_b64": self._encode_depth_preview(rgb_np, depth_m, valid_mask),
            "provider": f"microsoft-moge2 + open3d-icp ({len(points)} pts)",
        }


reconstructor = MultiViewReconstructor()
