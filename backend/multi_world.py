"""
Multi-World Spatial Reconstructor & Intersection Splicer:
- Builds individual leveled 3D worlds per walk-around video clip (saving keyframe JPEGs & 3D anchors)
- Discovers which videos share the same physical space via deduplicated SIFT + VGGT-1B 3D Joint-Bridge verification
- Rejects false matches via gravity alignment + 3D overlap fitness
- Splices intersecting clips into unified worlds while keeping disjoint spaces separate until bridged.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree
import torch

from walkthrough_vggt import (
    IncrementalVGGTReconstructor,
    _umeyama_sim3,
    auto_level_scene,
    enforce_room_envelope,
    predict_wall_architecture,
    predict_wall_segments,
    smooth_structural_planes,
)
from vggt.utils.load_fn import load_and_preprocess_images


def _slugify_clip(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(name).stem).strip("_") or f"clip_{int(time.time())}"


def _sim3_inlier_fraction(
    src: np.ndarray,
    dst: np.ndarray,
    tol_scale: float = 0.045,
) -> Tuple[float, np.ndarray, np.ndarray, float]:
    """Fits RANSAC Sim(3) and returns (scale, R, t, inlier_fraction)."""
    n = len(src)
    if n < 24:
        return 1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64), 0.0

    if n > 3500:
        step = max(1, n // 3500)
        src = src[::step]
        dst = dst[::step]
        n = len(src)

    rng = np.random.default_rng(42)
    dst_span = max(0.2, float(np.linalg.norm(np.percentile(dst, 90, axis=0) - np.percentile(dst, 10, axis=0))))
    tol = max(0.03, tol_scale * dst_span)

    best_inl = None
    best_cnt = -1
    for _ in range(50):
        idx = rng.choice(n, size=6, replace=False)
        s, R, t = _umeyama_sim3(src[idx], dst[idx])
        err = np.linalg.norm((s * (src @ R.T) + t) - dst, axis=1)
        inl = err < tol
        cnt = int(np.sum(inl))
        if cnt > best_cnt:
            best_cnt = cnt
            best_inl = inl

    if best_inl is None or best_cnt < 16:
        return 1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64), 0.0

    s, R, t = _umeyama_sim3(src[best_inl], dst[best_inl])
    err_f = np.linalg.norm((s * (src @ R.T) + t) - dst, axis=1)
    frac = float(np.mean(err_f < tol * 1.25))
    return s, R, t, frac


def _compose_sim3(
    s2: float, R2: np.ndarray, t2: np.ndarray,
    s1: float, R1: np.ndarray, t1: np.ndarray,
) -> Tuple[float, np.ndarray, np.ndarray]:
    """Composes T2(T1(x)) where T(x) = s * (R @ x) + t."""
    s = float(s2 * s1)
    R = R2 @ R1
    t = s2 * (R2 @ t1) + t2
    return s, R, t


class MultiWorldManager:
    def __init__(
        self,
        runner: IncrementalVGGTReconstructor,
        worlds_root: Path,
    ) -> None:
        self.runner = runner
        self.worlds_root = worlds_root
        self.clips_dir = worlds_root / "clips"
        self.merged_dir = worlds_root / "merged"
        self.index_path = worlds_root / "index.json"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.merged_dir.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> Dict[str, Any]:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"clips": {}, "edges": [], "worlds": [], "active_world_id": None}

    def save_index(self, idx: Dict[str, Any]) -> None:
        self.worlds_root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")

    def ingest_video(
        self,
        video_path: Path,
        clip_id: Optional[str] = None,
        max_keyframes: int = 24,
        max_points: int = 200000,
        force: bool = False,
        imu_session: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cid = clip_id or _slugify_clip(video_path.name)
        cdir = self.clips_dir / cid
        clip_json = cdir / "clip.json"
        anchor_npz = cdir / "frames_3d.npz"

        if not force and clip_json.exists() and anchor_npz.exists():
            data = json.loads(clip_json.read_text(encoding="utf-8"))
            print(f"[MultiWorld] Using cached clip '{cid}' ({len(data.get('points', [])):,} pts)")
        else:
            print(f"[MultiWorld] Reconstructing '{video_path.name}' -> clip '{cid}'...")
            data = self.runner.reconstruct_video_incremental(
                str(video_path),
                clip_json,
                window_size=8,
                overlap=3,
                max_keyframes=max_keyframes,
                max_points=max_points,
                conf_percentile=48.0,
                anchor_dir=cdir,
                imu_session=imu_session,
            )

        if data.get("imu_kf"):
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "imu_kf.json").write_text(json.dumps(data["imu_kf"]), encoding="utf-8")

        idx = self.load_index()
        idx["clips"][cid] = {
            "clip_id": cid,
            "source_name": video_path.name,
            "point_count": len(data.get("points", [])),
            "keyframe_count": len(data.get("keyframes", [])),
            "wall_count": len(data.get("wall_segments", [])),
            "elapsed_s": data.get("elapsed_s", 0.0),
            "imu_meta": data.get("imu_meta"),
            "json_path": str(clip_json),
            "dir": str(cdir),
        }
        self.save_index(idx)
        return idx["clips"][cid]

    def _compute_frame_descriptors(self, paths: List[str]) -> np.ndarray:
        """
        Extracts L2-normalized DINOv2 ViT-L patch-mean embeddings from VGGT's loaded
        visual backbone (~0.2s per clip) for viewpoint-robust place recognition.
        """
        if not paths:
            return np.zeros((0, 1024), dtype=np.float32)
        self.runner._load_model()
        patch_embed = self.runner._model.aggregator.patch_embed
        imgs = load_and_preprocess_images(paths, mode="crop").to(self.runner._device)
        with torch.inference_mode():
            if self.runner._device == "cuda":
                cap_major = torch.cuda.get_device_capability()[0]
                dtype = torch.bfloat16 if cap_major >= 8 else torch.float16
                with torch.amp.autocast("cuda", dtype=dtype):
                    toks = patch_embed(imgs)
            else:
                toks = patch_embed(imgs)
            if isinstance(toks, dict):
                toks = toks["x_norm_patchtokens"]
            desc = toks.mean(dim=1).float()
            desc = desc / (desc.norm(dim=-1, keepdim=True) + 1e-8)
        return desc.cpu().numpy()

    @staticmethod
    def _burst_window(center: int, n_tot: int, width: int = 4) -> List[int]:
        w = min(width, n_tot)
        start = max(0, min(n_tot - w, center - 1))
        return list(range(start, start + w))

    def _candidate_anchor_bursts(
        self,
        emb_a: np.ndarray,
        emb_b: np.ndarray,
        min_sim: float = 0.945,
        max_candidates: int = 2,
    ) -> Tuple[float, List[Tuple[float, List[int], List[int]]]]:
        """
        Finds up to `max_candidates` well-separated contiguous 4-frame anchor bursts
        between Clip A and Clip B sorted by DINOv2 cosine similarity.
        """
        if len(emb_a) == 0 or len(emb_b) == 0:
            return 0.0, []
        sim_mat = emb_a @ emb_b.T
        best_sim = float(np.max(sim_mat))
        if best_sim < min_sim:
            return best_sim, []

        flat_order = np.argsort(sim_mat.ravel())[::-1]
        cands: List[Tuple[float, List[int], List[int]]] = []
        used_centers: List[Tuple[int, int]] = []
        nb = sim_mat.shape[1]
        for flat_i in flat_order:
            ia = int(flat_i // nb)
            ib = int(flat_i % nb)
            s = float(sim_mat[ia, ib])
            if s < min_sim:
                break
            if any(abs(ia - ua) < 4 and abs(ib - ub) < 4 for ua, ub in used_centers):
                continue
            used_centers.append((ia, ib))
            cands.append((
                s,
                self._burst_window(ia, len(emb_a), 4),
                self._burst_window(ib, len(emb_b), 4),
            ))
            if len(cands) >= max_candidates:
                break
        return best_sim, cands

    def _verify_3d_bridge(
        self,
        cid_a: str,
        cid_b: str,
        idx_a: List[int],
        idx_b: List[int],
    ) -> Optional[Dict[str, Any]]:
        """
        Runs ONE joint VGGT-1B pass on [A anchors + B anchors], computes T_B_to_A via Sim(3),
        and validates gravity agreement + real 3D cloud overlap fitness before accepting.
        """
        anc_a = np.load(self.clips_dir / cid_a / "frames_3d.npz", allow_pickle=False)
        anc_b = np.load(self.clips_dir / cid_b / "frames_3d.npz", allow_pickle=False)

        paths_a = [str(p) for p in anc_a["paths"][idx_a]]
        paths_b = [str(p) for p in anc_b["paths"][idx_b]]
        na, nb = len(paths_a), len(paths_b)

        res = self.runner._infer_window(paths_a + paths_b, conf_percentile=46.0)
        joint_pts_gl = res["world_pts"] * np.array([1.0, -1.0, -1.0], dtype=np.float64)
        joint_masks = res["valid_masks"]
        joint_cols = res["imgs_rgb"]

        # Gate 0 (Scale-Shrink-Proof): check A-anchors vs B-anchors directly inside joint VGGT space
        pA = joint_pts_gl[:na][joint_masks[:na]][::8]
        cA = joint_cols[:na][joint_masks[:na]][::8]
        pB = joint_pts_gl[na:][joint_masks[na:]][::8]
        cB = joint_cols[na:][joint_masks[na:]][::8]
        if len(pA) < 100 or len(pB) < 100:
            return None

        j_span = max(
            0.3,
            float(np.linalg.norm(np.percentile(np.vstack([pA, pB]), 90, axis=0) - np.percentile(np.vstack([pA, pB]), 10, axis=0))),
        )
        j_tol = max(0.05, 0.04 * j_span)
        tree_a = cKDTree(pA)
        tree_b = cKDTree(pB)

        d_b2a, idx_a_nn = tree_a.query(pB, distance_upper_bound=j_tol)
        vis_b = np.isfinite(d_b2a)
        col_b = vis_b & (np.linalg.norm(cB - cA[np.minimum(idx_a_nn, len(cA) - 1)], axis=1) < 0.16)

        d_a2b, idx_b_nn = tree_b.query(pA, distance_upper_bound=j_tol)
        vis_a = np.isfinite(d_a2b)
        col_a = vis_a & (np.linalg.norm(cA - cB[np.minimum(idx_b_nn, len(cB) - 1)], axis=1) < 0.16)

        joint_geo_min = min(float(np.mean(vis_a)), float(np.mean(vis_b)))
        joint_col_min = min(float(np.mean(col_a)), float(np.mean(col_b)))
        if joint_geo_min < 0.10 or joint_col_min < 0.04:
            return None

        # Fit Sim(3): joint[0..na) -> Clip A world, and Clip B world -> joint[na..na+nb)
        src_jA, dst_jA = [], []
        for k, ia in enumerate(idx_a):
            jp = joint_pts_gl[k, ::2, ::2]
            jm = joint_masks[k, ::2, ::2]
            ap = anc_a["pts"][ia].astype(np.float64)
            am = anc_a["masks"][ia]
            h_m = min(jp.shape[0], ap.shape[0])
            w_m = min(jp.shape[1], ap.shape[1])
            both = jm[:h_m, :w_m] & am[:h_m, :w_m]
            if np.any(both):
                src_jA.append(jp[:h_m, :w_m][both])
                dst_jA.append(ap[:h_m, :w_m][both])

        src_Bj, dst_Bj = [], []
        for k, ib in enumerate(idx_b):
            jp = joint_pts_gl[na + k, ::2, ::2]
            jm = joint_masks[na + k, ::2, ::2]
            bp = anc_b["pts"][ib].astype(np.float64)
            bm = anc_b["masks"][ib]
            h_m = min(jp.shape[0], bp.shape[0])
            w_m = min(jp.shape[1], bp.shape[1])
            both = jm[:h_m, :w_m] & bm[:h_m, :w_m]
            if np.any(both):
                src_Bj.append(bp[:h_m, :w_m][both])
                dst_Bj.append(jp[:h_m, :w_m][both])

        if not src_jA or not src_Bj:
            return None

        s_jA, R_jA, t_jA, frac_jA = _sim3_inlier_fraction(np.concatenate(src_jA), np.concatenate(dst_jA))
        s_Bj, R_Bj, t_Bj, frac_Bj = _sim3_inlier_fraction(np.concatenate(src_Bj), np.concatenate(dst_Bj))
        if frac_jA < 0.30 or frac_Bj < 0.30:
            return None

        s_BA, R_BA, t_BA = _compose_sim3(s_jA, R_jA, t_jA, s_Bj, R_Bj, t_Bj)
        if not (0.20 <= s_BA <= 5.0):
            return None

        # Gate 1: Gravity +Y alignment check (floors shouldn't twist sideways or invert)
        up_vec = R_BA @ np.array([0.0, 1.0, 0.0], dtype=np.float64)
        up_dot = float(up_vec[1])
        if up_dot < 0.85:
            return None

        # Gate 2: Real 3D point cloud overlap + fine ICP lock
        clip_a_data = json.loads((self.clips_dir / cid_a / "clip.json").read_text(encoding="utf-8"))
        clip_b_data = json.loads((self.clips_dir / cid_b / "clip.json").read_text(encoding="utf-8"))
        pts_a = np.asarray(clip_a_data["points"], dtype=np.float64)[:, :3]
        pts_b = np.asarray(clip_b_data["points"], dtype=np.float64)[:, :3]
        if len(pts_a) < 200 or len(pts_b) < 200:
            return None

        pts_b_tr = s_BA * (pts_b[::3] @ R_BA.T) + t_BA
        pcd_a = o3d.geometry.PointCloud()
        pcd_a.points = o3d.utility.Vector3dVector(pts_a[::3])
        pcd_b = o3d.geometry.PointCloud()
        pcd_b.points = o3d.utility.Vector3dVector(pts_b_tr)

        a_span = max(0.5, float(np.linalg.norm(np.percentile(pts_a, 90, axis=0) - np.percentile(pts_a, 10, axis=0))))
        eval_tol = max(0.08, 0.055 * a_span)
        eval_res = o3d.pipelines.registration.evaluate_registration(pcd_b, pcd_a, max_correspondence_distance=eval_tol)
        eval_rev = o3d.pipelines.registration.evaluate_registration(pcd_a, pcd_b, max_correspondence_distance=eval_tol)
        min_fitness = min(float(eval_res.fitness), float(eval_rev.fitness))
        max_fitness = max(float(eval_res.fitness), float(eval_rev.fitness))

        if min_fitness < 0.05 or max_fitness < 0.16:
            return None

        # Refine rigid alignment via Point-to-Plane ICP (fall back to Point-to-Point if normals struggle)
        pcd_a.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=eval_tol * 1.2, max_nn=24))
        pcd_b.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=eval_tol * 1.2, max_nn=24))
        reg = o3d.pipelines.registration.registration_icp(
            pcd_b, pcd_a, max_correspondence_distance=eval_tol, init=np.eye(4),
            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(),
            criteria=o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=40),
        )
        if float(reg.fitness) < 0.85 * float(eval_res.fitness):
            reg = o3d.pipelines.registration.registration_icp(
                pcd_b, pcd_a, max_correspondence_distance=eval_tol, init=np.eye(4),
                estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPoint(),
                criteria=o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=35),
            )
        R_icp = reg.transformation[:3, :3]
        t_icp = reg.transformation[:3, 3]
        s_BA, R_BA, t_BA = _compose_sim3(1.0, R_icp, t_icp, s_BA, R_BA, t_BA)

        # Gate 3 (Physics Feasibility): Clip B's camera trajectory cannot pierce solid walls of Clip A
        kfs_b = clip_b_data.get("keyframes", [])
        if kfs_b and len(pts_a) >= 400:
            cams_b = np.array([kf["pose"] for kf in kfs_b], dtype=np.float64)
            cams_b_tr = s_BA * (cams_b @ R_BA.T) + t_BA
            y_lo = float(np.percentile(pts_a[:, 1], 10)) + 0.08
            y_hi = float(np.percentile(pts_a[:, 1], 92))
            solid_a = pts_a[(pts_a[:, 1] >= y_lo) & (pts_a[:, 1] <= y_hi)][::3]
            if len(solid_a) >= 200:
                t_solid = cKDTree(solid_a)
                cam_dists, _ = t_solid.query(cams_b_tr)
                collided = float(np.mean(cam_dists < max(0.025, 0.015 * a_span)))
                if collided > 0.20:
                    return None

        return {
            "clip_a": cid_a,
            "clip_b": cid_b,
            "scale": round(float(s_BA), 6),
            "R": [[round(float(x), 6) for x in row] for row in R_BA],
            "t": [round(float(x), 6) for x in t_BA],
            "up_dot": round(up_dot, 3),
            "joint_geo_min": round(joint_geo_min, 3),
            "joint_col_min": round(joint_col_min, 3),
            "overlap_fitness": round(float(max(max_fitness, reg.fitness)), 3),
            "anchor_inliers": (round(frac_jA, 3), round(frac_Bj, 3)),
        }

    def reconcile_worlds(self, max_merged_points: int = 320000) -> Dict[str, Any]:
        """
        Matches all ingested clips via DINOv2 place embeddings + VGGT 3D Joint-Bridge checks,
        computes connected components, and splices intersecting clips into unified worlds
        while keeping disjoint physical spaces separate.
        """
        idx = self.load_index()
        cids = sorted(idx.get("clips", {}).keys())
        if not cids:
            idx["edges"] = []
            idx["worlds"] = []
            self.save_index(idx)
            return idx

        clip_embs: Dict[str, np.ndarray] = {}
        for cid in cids:
            anc = np.load(self.clips_dir / cid / "frames_3d.npz", allow_pickle=False)
            paths = [str(p) for p in anc["paths"]]
            clip_embs[cid] = self._compute_frame_descriptors(paths)

        verified_edges: List[Dict[str, Any]] = []
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                ca, cb = cids[i], cids[j]
                best_sim, bursts = self._candidate_anchor_bursts(clip_embs[ca], clip_embs[cb])
                print(f"[MultiWorld] Pair {ca} <-> {cb}: DINOv2 max_sim={best_sim:.3f} candidates={len(bursts)}")
                bridge = None
                for sim_score, ia, ib in bursts:
                    bridge = self._verify_3d_bridge(ca, cb, ia, ib)
                    if bridge is not None:
                        bridge["dino_sim"] = round(sim_score, 4)
                        verified_edges.append(bridge)
                        print(
                            f"  -> VERIFIED INTERSECTION! joint_geo={bridge['joint_geo_min']} "
                            f"joint_col={bridge['joint_col_min']} fitness={bridge['overlap_fitness']} "
                            f"up_dot={bridge['up_dot']} scale={bridge['scale']}"
                        )
                        break
                if bursts and bridge is None:
                    print("  -> Rejected by 3D joint geometry/color check (disjoint physical spaces).")

        # Connected components over clips
        adj: Dict[str, List[Tuple[str, float, np.ndarray, np.ndarray]]] = {cid: [] for cid in cids}
        for e in verified_edges:
            ca, cb = e["clip_a"], e["clip_b"]
            s_BA = float(e["scale"])
            R_BA = np.asarray(e["R"], dtype=np.float64)
            t_BA = np.asarray(e["t"], dtype=np.float64)
            adj[ca].append((cb, s_BA, R_BA, t_BA))
            # Inverse T_A_to_B: x_B = (1/s) * R.T @ (x_A - t)
            s_AB = 1.0 / s_BA
            R_AB = R_BA.T
            t_AB = -s_AB * (R_AB @ t_BA)
            adj[cb].append((ca, s_AB, R_AB, t_AB))

        visited = set()
        components: List[List[str]] = []
        for cid in cids:
            if cid in visited:
                continue
            stack = [cid]
            comp = []
            visited.add(cid)
            while stack:
                u = stack.pop()
                comp.append(u)
                for v, _, _, _ in adj[u]:
                    if v not in visited:
                        visited.add(v)
                        stack.append(v)
            comp.sort()
            components.append(comp)

        # Clean stale merged world files
        for old_f in self.merged_dir.glob("world_*.json"):
            old_f.unlink()

        worlds_meta: List[Dict[str, Any]] = []
        # Sort so spliced multi-clip worlds come first
        components.sort(key=lambda c: (-len(c), c[0]))

        for w_num, comp in enumerate(components, start=1):
            wid = f"world_{w_num}"
            out_json = self.merged_dir / f"{wid}.json"
            if len(comp) == 1:
                cid = comp[0]
                cdata = json.loads((self.clips_dir / cid / "clip.json").read_text(encoding="utf-8"))
                out_json.write_text(json.dumps(cdata), encoding="utf-8")
                worlds_meta.append({
                    "world_id": wid,
                    "label": f"{cid} (Separate Space)",
                    "status": "separate",
                    "clips": comp,
                    "point_count": len(cdata.get("points", [])),
                    "keyframe_count": len(cdata.get("keyframes", [])),
                    "wall_count": len(cdata.get("wall_segments", [])),
                    "opening_count": len(cdata.get("predicted_openings", [])),
                    "imu_meta": cdata.get("imu_meta"),
                    "json_path": str(out_json),
                })
                continue

            # Preferred seamless splice: sample up to 24 keyframes across all clips in `comp`
            # and solve ONE global unified VGGT-1B pass with zero stitching seam or ICP drift.
            total_avail = 0
            clip_paths_dict: Dict[str, List[str]] = {}
            clip_imu_dict: Dict[str, Optional[List[Dict[str, Any]]]] = {}
            for cid in comp:
                anc = np.load(self.clips_dir / cid / "frames_3d.npz", allow_pickle=False)
                p_list = [str(p) for p in anc["paths"]]
                clip_paths_dict[cid] = p_list
                total_avail += len(p_list)
                imu_kf_p = self.clips_dir / cid / "imu_kf.json"
                if imu_kf_p.exists():
                    try:
                        loaded_ik = json.loads(imu_kf_p.read_text(encoding="utf-8"))
                        if isinstance(loaded_ik, list) and len(loaded_ik) == len(p_list):
                            clip_imu_dict[cid] = loaded_ik
                        else:
                            clip_imu_dict[cid] = None
                    except Exception:
                        clip_imu_dict[cid] = None
                else:
                    clip_imu_dict[cid] = None

            short_names = " + ".join(c[:15] for c in comp)
            if len(comp) <= 4 and total_avail >= 4:
                budget = 24
                merged_paths: List[str] = []
                merged_clips_tag: List[str] = []
                merged_imu_kf: List[Optional[Dict[str, Any]]] = []
                # Distribute 24 frames proportionally (longest clips get slightly more)
                allocs = []
                for cid in comp:
                    frac = len(clip_paths_dict[cid]) / max(1, total_avail)
                    n_c = max(min(len(clip_paths_dict[cid]), 8), int(round(budget * frac)))
                    allocs.append(min(len(clip_paths_dict[cid]), n_c))
                while sum(allocs) > budget:
                    mx_i = int(np.argmax(allocs))
                    allocs[mx_i] -= 1
                while sum(allocs) < min(budget, total_avail):
                    for k_i, cid in enumerate(comp):
                        if allocs[k_i] < len(clip_paths_dict[cid]) and sum(allocs) < budget:
                            allocs[k_i] += 1

                for cid, n_c in zip(comp, allocs):
                    p_list = clip_paths_dict[cid]
                    i_list = clip_imu_dict.get(cid)
                    sel = np.linspace(0, len(p_list) - 1, n_c, dtype=int)
                    for si in sel:
                        merged_paths.append(p_list[int(si)])
                        merged_clips_tag.append(cid)
                        merged_imu_kf.append(i_list[int(si)] if i_list is not None else None)

                use_imu_kf = merged_imu_kf if any(x is not None for x in merged_imu_kf) else None
                print(f"[MultiWorld] Solving unified Global VGGT splice for {comp}: {allocs} frames...")
                merged_payload = self.runner.reconstruct_frames_global(
                    merged_paths,
                    output_json=None,
                    max_points=max_merged_points,
                    target_span=3.0,
                    clip_ids_per_frame=merged_clips_tag,
                    imu_kf=use_imu_kf,
                )
                merged_payload["clips"] = comp
                out_json.write_text(json.dumps(merged_payload), encoding="utf-8")

                pts_arr = np.asarray(merged_payload["points"], dtype=np.float64)
                if len(pts_arr) > 0:
                    ply_out = out_json.with_suffix(".ply")
                    out_pcd = o3d.geometry.PointCloud()
                    out_pcd.points = o3d.utility.Vector3dVector(pts_arr[:, :3])
                    out_pcd.colors = o3d.utility.Vector3dVector(pts_arr[:, 3:6])
                    o3d.io.write_point_cloud(str(ply_out), out_pcd)

                worlds_meta.append({
                    "world_id": wid,
                    "label": f"Spliced ({short_names})",
                    "status": "spliced",
                    "clips": comp,
                    "point_count": len(merged_payload.get("points", [])),
                    "keyframe_count": len(merged_payload.get("keyframes", [])),
                    "wall_count": len(merged_payload.get("wall_segments", [])),
                    "opening_count": len(merged_payload.get("predicted_openings", [])),
                    "imu_meta": merged_payload.get("imu_meta"),
                    "json_path": str(out_json),
                })
                continue

            # Fallback transform fusion when merging > 4 very large clips
            root = max(comp, key=lambda c: idx["clips"][c]["point_count"])
            transforms: Dict[str, Tuple[float, np.ndarray, np.ndarray]] = {
                root: (1.0, np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64))
            }
            bfs = [root]
            while bfs:
                u = bfs.pop(0)
                su, Ru, tu = transforms[u]
                for v, s_vu, R_vu, t_vu in adj[u]:
                    if v not in transforms:
                        transforms[v] = _compose_sim3(su, Ru, tu, s_vu, R_vu, t_vu)
                        bfs.append(v)

            merged_pcd = o3d.geometry.PointCloud()
            merged_kfs: List[Dict[str, Any]] = []
            for cid in comp:
                sc, Rc, tc = transforms[cid]
                cdata = json.loads((self.clips_dir / cid / "clip.json").read_text(encoding="utf-8"))
                arr = np.asarray(cdata.get("points", []), dtype=np.float64)
                if len(arr) > 0:
                    pts_tr = sc * (arr[:, :3] @ Rc.T) + tc
                    cols_tr = arr[:, 3:6]
                    cpcd = o3d.geometry.PointCloud()
                    cpcd.points = o3d.utility.Vector3dVector(pts_tr)
                    cpcd.colors = o3d.utility.Vector3dVector(cols_tr)
                    merged_pcd = merged_pcd + cpcd
                for kf in cdata.get("keyframes", []):
                    kf_c = dict(kf)
                    p_tr = sc * (Rc @ np.asarray(kf["pose"], dtype=np.float64)) + tc
                    kf_c["pose"] = [round(float(x), 3) for x in p_tr]
                    kf_c["clip_id"] = cid
                    merged_kfs.append(kf_c)

            pts_all = np.asarray(merged_pcd.points)
            span_all = max(0.3, float(np.linalg.norm(np.percentile(pts_all, 90, axis=0) - np.percentile(pts_all, 10, axis=0))))
            merged_pcd = merged_pcd.voxel_down_sample(voxel_size=max(0.002, span_all / 420.0))
            if len(merged_pcd.points) >= 100:
                merged_pcd, _ = merged_pcd.remove_statistical_outlier(nb_neighbors=24, std_ratio=1.25)

            pts_np = np.asarray(merged_pcd.points)
            cols_np = np.asarray(merged_pcd.colors)
            pts_np, merged_kfs = auto_level_scene(pts_np, merged_kfs, target_span=3.0)
            pts_np = smooth_structural_planes(pts_np)

            if len(pts_np) > max_merged_points:
                sel = np.linspace(0, len(pts_np) - 1, max_merged_points, dtype=int)
                pts_np, cols_np = pts_np[sel], cols_np[sel]

            pts_np, cols_np, room_env = (
                enforce_room_envelope(pts_np, cols_np, merged_kfs)
                if len(pts_np) > 0
                else (pts_np, cols_np, {"wall_segments": [], "predicted_openings": [], "room_polygon": [], "floor_y": 0.0, "roof_y": 1.0})
            )
            wall_segs = room_env["wall_segments"]
            pred_openings = room_env["predicted_openings"]
            pts_list = [
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

            merged_payload = {
                "points": pts_list,
                "keyframes": merged_kfs,
                "wall_segments": wall_segs,
                "predicted_openings": pred_openings,
                "room_envelope": {
                    "room_polygon": room_env["room_polygon"],
                    "floor_y": room_env["floor_y"],
                    "roof_y": room_env["roof_y"],
                },
                "clips": comp,
                "device": "cuda",
                "elapsed_s": round(sum(idx["clips"][c].get("elapsed_s", 0.0) for c in comp), 2),
            }
            out_json.write_text(json.dumps(merged_payload), encoding="utf-8")

            ply_out = out_json.with_suffix(".ply")
            out_pcd = o3d.geometry.PointCloud()
            out_pcd.points = o3d.utility.Vector3dVector(pts_np)
            out_pcd.colors = o3d.utility.Vector3dVector(cols_np)
            o3d.io.write_point_cloud(str(ply_out), out_pcd)

            worlds_meta.append({
                "world_id": wid,
                "label": f"Spliced ({short_names})",
                "status": "spliced",
                "clips": comp,
                "point_count": len(pts_list),
                "keyframe_count": len(merged_kfs),
                "wall_count": len(wall_segs),
                "opening_count": len(pred_openings),
                "json_path": str(out_json),
            })

        idx["edges"] = verified_edges
        idx["worlds"] = worlds_meta
        if worlds_meta:
            idx["active_world_id"] = worlds_meta[0]["world_id"]
        self.save_index(idx)
        return idx


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a != "--force"]
    force_rebuild = "--force" in sys.argv[1:]
    folder = Path(args[0]) if args else Path(r"C:\Users\conor\Downloads\Send Anywhere (2026-11-14 02-45-06)")
    worlds_root = Path(__file__).resolve().parent / "static" / "worlds"
    runner = IncrementalVGGTReconstructor()
    mgr = MultiWorldManager(runner=runner, worlds_root=worlds_root)

    videos = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".mkv")])
    for v in videos:
        mgr.ingest_video(v, max_keyframes=24, max_points=260000, force=force_rebuild)

    result = mgr.reconcile_worlds(max_merged_points=320000)
    print("\n=== MULTI-WORLD GRAPH SUMMARY ===")
    print(f"Total clips: {len(result['clips'])} | Verified intersections: {len(result['edges'])}")
    for w in result["worlds"]:
        print(
            f"  * {w['world_id']} [{w['status']}]: {w['clips']} -> "
            f"{w['point_count']:,} pts, {w['wall_count']} walls, {w.get('opening_count', 0)} openings"
        )
