"""
9-Axis IMU Sensor Fusion & Video-IMU Temporal Alignment for Meta Ray-Ban Display
--------------------------------------------------------------------------------
Combines:
- 3-axis Gyroscope (angular velocity deg/s -> Earth-vertical drift-free yaw delta)
- 3-axis Accelerometer (gravity vector for exact roll/pitch + linear step peak counting)
- 3-axis Magnetometer / Compass (absolute heading with metallic outlier rejection)

Pairs logged browser IMU telemetry (`POST /api/imu-sessions`) with uploaded `.mp4`
videos via UTC timestamp matching + optical-flow vs gyro angular speed cross-correlation,
then supplies per-keyframe gravity Up, anti-bend relative gyro yaw, compass alignment,
and step-calibrated walking distance to `walkthrough_vggt.py` and `spatial_model.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def wrap_deg(angle: float) -> float:
    return ((float(angle) + 180.0) % 360.0) - 180.0


def unwrap_deg_series(angles_deg: np.ndarray) -> np.ndarray:
    if len(angles_deg) == 0:
        return angles_deg
    return np.rad2deg(np.unwrap(np.deg2rad(angles_deg)))


def parse_video_start_epoch_ms(video_path: Path) -> Optional[float]:
    """
    Extracts UTC recording start timestamp in milliseconds from Meta / Pixel / Android
    filenames (`YYYYMMDD_HHMMSS` or `PXL_YYYYMMDD_HHMMSS`) or MP4 mvhd/file mtime.
    Note: Meta glasses filenames (`20261114_022040_00cb8f59.mp4`) are local wall-clock
    time on the phone/glasses, so we compare against local datetime cleanly.
    """
    m = re.search(r"(?:^|\D)(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?:\D|$)", video_path.name)
    if m:
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
        try:
            dt_local = datetime(y, mo, d, hh, mm, ss)
            return dt_local.timestamp() * 1000.0
        except ValueError:
            pass
    try:
        return video_path.stat().st_mtime * 1000.0
    except OSError:
        return None


class ImuSessionStore:
    """Disk-backed store for Meta Display glasses IMU walk sessions."""

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def save_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        samples = payload.get("samples") or []
        now_ms = time.time() * 1000.0
        started_at_ms = float(
            payload.get("started_at_ms")
            or (samples[0].get("t_ms") if samples else now_ms)
        )
        ended_at_ms = float(
            payload.get("ended_at_ms")
            or (samples[-1].get("t_ms") if samples else now_ms)
        )
        raw_id = str(payload.get("session_id") or f"imu_{int(started_at_ms)}")
        session_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_id).strip("_") or f"imu_{int(now_ms)}"

        steps = max((int(s.get("step_count", 0)) for s in samples), default=int(payload.get("step_count", 0)))
        compass_vals = [float(s["compass_deg"]) for s in samples if s.get("compass_deg") is not None]
        avg_compass = round(float(np.median(compass_vals)), 1) if compass_vals else None

        doc = {
            "session_id": session_id,
            "device": str(payload.get("device") or "meta-ray-ban-display"),
            "started_at_ms": started_at_ms,
            "ended_at_ms": ended_at_ms,
            "duration_s": round(max(0.0, (ended_at_ms - started_at_ms) / 1000.0), 2),
            "sample_count": len(samples),
            "step_count": steps,
            "avg_compass_deg": avg_compass,
            "has_absolute_compass": any(bool(s.get("absolute")) for s in samples),
            "samples": samples,
        }
        out_path = self.sessions_dir / f"{session_id}.json"
        out_path.write_text(json.dumps(doc), encoding="utf-8")
        return {k: v for k, v in doc.items() if k != "samples"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for p in sorted(self.sessions_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                rows.append({
                    "session_id": data.get("session_id", p.stem),
                    "device": data.get("device", "unknown"),
                    "started_at_ms": data.get("started_at_ms", 0.0),
                    "ended_at_ms": data.get("ended_at_ms", 0.0),
                    "duration_s": data.get("duration_s", 0.0),
                    "sample_count": data.get("sample_count", 0),
                    "step_count": data.get("step_count", 0),
                    "avg_compass_deg": data.get("avg_compass_deg"),
                    "has_absolute_compass": data.get("has_absolute_compass", False),
                })
            except Exception:
                continue
        return rows

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", session_id).strip("_")
        p = self.sessions_dir / f"{safe_id}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def match_video_session(
        self,
        video_path: Path,
        video_duration_s: float = 0.0,
        explicit_session_id: Optional[str] = None,
        max_gap_ms: float = 20 * 60 * 1000.0,
    ) -> Optional[Dict[str, Any]]:
        if explicit_session_id and explicit_session_id not in ("", "auto", "none"):
            if explicit_session_id == "latest":
                sessions = self.list_sessions()
                return self.load_session(sessions[0]["session_id"]) if sessions else None
            return self.load_session(explicit_session_id)

        if explicit_session_id == "none":
            return None

        sessions = self.list_sessions()
        if not sessions:
            return None

        vid_start_ms = parse_video_start_epoch_ms(video_path)
        if vid_start_ms is None:
            return None
        vid_end_ms = vid_start_ms + max(1.0, video_duration_s) * 1000.0

        best_id: Optional[str] = None
        best_score = float("inf")
        for sess in sessions:
            if int(sess.get("sample_count", 0)) < 5:
                continue
            s0 = float(sess.get("started_at_ms", 0.0))
            s1 = float(sess.get("ended_at_ms", s0))
            # Check interval overlap first; otherwise distance between intervals
            overlap = min(vid_end_ms, s1) - max(vid_start_ms, s0)
            if overlap > 0:
                gap = -overlap  # larger overlap => smaller score
            else:
                gap = max(s0 - vid_end_ms, vid_start_ms - s1)
            if gap <= max_gap_ms and gap < best_score:
                best_score = gap
                best_id = sess["session_id"]

        return self.load_session(best_id) if best_id else None


def _extract_gyro_and_gravity(samples: List[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    """Converts raw JSON IMU samples into sorted numpy arrays with Earth-vertical yaw."""
    valid = sorted(
        [s for s in samples if isinstance(s, dict) and s.get("t_ms") is not None],
        key=lambda x: float(x["t_ms"]),
    )
    n = len(valid)
    t0 = float(valid[0]["t_ms"])
    t_s = np.array([(float(s["t_ms"]) - t0) / 1000.0 for s in valid], dtype=np.float64)

    gyro_dps = np.zeros((n, 3), dtype=np.float64)
    accel_g = np.zeros((n, 3), dtype=np.float64)
    compass_deg = np.full(n, np.nan, dtype=np.float64)
    pitch_deg = np.zeros(n, dtype=np.float64)
    roll_deg = np.zeros(n, dtype=np.float64)
    steps = np.zeros(n, dtype=np.float64)
    yaw_ui = np.full(n, np.nan, dtype=np.float64)

    for i, s in enumerate(valid):
        rot = s.get("gyro_dps") or s.get("rotationRate") or [0.0, 0.0, 0.0]
        gyro_dps[i] = [float(rot[0] or 0.0), float(rot[1] or 0.0), float(rot[2] or 0.0)]
        ag = s.get("accel_g") or s.get("accelerationIncludingGravity") or [0.0, 9.81, 0.0]
        accel_g[i] = [float(ag[0] or 0.0), float(ag[1] or 0.0), float(ag[2] or 0.0)]
        if s.get("compass_deg") is not None:
            compass_deg[i] = float(s["compass_deg"])
        elif s.get("alpha") is not None:
            compass_deg[i] = float(s["alpha"])
        if s.get("yaw_deg") is not None:
            yaw_ui[i] = float(s["yaw_deg"])
        pitch_deg[i] = float(s.get("pitch_deg") or s.get("beta") or 0.0)
        roll_deg[i] = float(s.get("roll_deg") or s.get("gamma") or 0.0)
        steps[i] = float(s.get("step_count") or 0.0)

    # Project gyro rate onto normalized gravity vector for Earth-vertical yaw rate
    yaw_rate_earth = np.zeros(n, dtype=np.float64)
    omega_norm = np.linalg.norm(gyro_dps, axis=1)
    for i in range(n):
        gn = float(np.linalg.norm(accel_g[i]))
        if gn > 3.0 and float(omega_norm[i]) > 1e-4:
            g_hat = accel_g[i] / gn
            # In DeviceMotion coords: yaw is primarily projection along Earth up/gravity
            yaw_rate_earth[i] = float(np.dot(gyro_dps[i], g_hat))
        else:
            yaw_rate_earth[i] = float(gyro_dps[i, 0])

    # Integrate Earth-vertical gyro yaw cleanly
    gyro_yaw = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        dt = float(np.clip(t_s[i] - t_s[i - 1], 0.0, 0.25))
        if abs(yaw_rate_earth[i]) < 0.35:  # bias deadband below ~0.35 deg/s stationary drift
            dy = 0.0
        else:
            dy = 0.5 * (yaw_rate_earth[i - 1] + yaw_rate_earth[i]) * dt
        gyro_yaw[i] = gyro_yaw[i - 1] + dy

    # If raw rotationRate was all-zeros (e.g. orientation-only device), use unwrapped UI/compass yaw
    if float(np.max(np.abs(gyro_yaw))) < 0.5 and np.any(np.isfinite(yaw_ui)):
        valid_y = np.where(np.isfinite(yaw_ui))[0]
        unw = unwrap_deg_series(yaw_ui[valid_y])
        gyro_yaw = np.interp(t_s, t_s[valid_y], unw)
        omega_norm = np.abs(np.gradient(gyro_yaw, np.maximum(t_s, 1e-3)))

    # Anchor compass while rejecting sudden metallic interference (> 18 deg divergence vs gyro)
    good_c = np.where(np.isfinite(compass_deg))[0]
    cleaned_compass = np.full(n, np.nan, dtype=np.float64)
    if len(good_c) >= 3:
        c_unw = unwrap_deg_series(compass_deg[good_c])
        # Offset aligning gyro trajectory to median compass heading
        diffs = np.array([wrap_deg(c_unw[k] - gyro_yaw[good_c[k]]) for k in range(len(good_c))], dtype=np.float64)
        base_offset = float(np.median(diffs))
        inliers = np.abs([wrap_deg(d - base_offset) for d in diffs]) <= 18.0
        if np.any(inliers):
            base_offset = float(np.median(diffs[inliers]))
        cleaned_compass = gyro_yaw + base_offset
    elif np.any(np.isfinite(yaw_ui)):
        base_y = float(np.nanmedian(yaw_ui))
        cleaned_compass = gyro_yaw + base_y
    else:
        cleaned_compass = gyro_yaw.copy()

    return {
        "t_s": t_s,
        "gyro_dps": gyro_dps,
        "omega_norm": omega_norm,
        "accel_g": accel_g,
        "gyro_yaw_deg": gyro_yaw,
        "compass_yaw_deg": cleaned_compass,
        "pitch_deg": pitch_deg,
        "roll_deg": roll_deg,
        "steps": steps,
    }


def _estimate_video_angular_speed_series(video_path: str, sample_fps: float = 4.0) -> Tuple[np.ndarray, np.ndarray]:
    """Computes visual frame-to-frame optical rotation speed curve for clock alignment."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return np.zeros(0), np.zeros(0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    if fps <= 1.0 or fps > 240.0:
        fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(fps / max(1.0, sample_fps))))

    orb = cv2.ORB_create(nfeatures=240)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    times_s: List[float] = []
    vis_dps: List[float] = []

    prev_kp, prev_des, prev_t = None, None, 0.0
    idx = 0
    while idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        t_cur = idx / fps
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (240, max(135, int(240 * h / max(1, w)))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        kp, des = orb.detectAndCompute(gray, None)

        if prev_des is not None and des is not None and len(prev_kp) >= 12 and len(kp) >= 12:
            matches = bf.match(prev_des, des)
            if len(matches) >= 10:
                matches = sorted(matches, key=lambda m: m.distance)[:48]
                dxy = np.array(
                    [[kp[m.trainIdx].pt[0] - prev_kp[m.queryIdx].pt[0], kp[m.trainIdx].pt[1] - prev_kp[m.queryIdx].pt[1]] for m in matches],
                    dtype=np.float64,
                )
                med_shift_px = float(np.linalg.norm(np.median(dxy, axis=0)))
                dt = max(0.05, t_cur - prev_t)
                # Approximate ~75 deg horizontal FOV over 240px width -> deg/s
                est_dps = (med_shift_px * (75.0 / 240.0)) / dt
                times_s.append(0.5 * (t_cur + prev_t))
                vis_dps.append(min(180.0, est_dps))

        prev_kp, prev_des, prev_t = kp, des, t_cur
        idx += step
    cap.release()
    return np.asarray(times_s, dtype=np.float64), np.asarray(vis_dps, dtype=np.float64)


def align_video_to_imu(
    video_path: str,
    imu_session: Dict[str, Any],
) -> Tuple[Optional[Dict[str, np.ndarray]], float]:
    """
    Prepares cleaned IMU time series and automatically finds the optimal clock skew
    `offset_s` (imu_t = video_t + offset_s) by cross-correlating visual pixel flow
    rotation speed against gyro `||omega||`.
    """
    samples = imu_session.get("samples") or []
    if len(samples) < 5:
        return None, 0.0

    imu = _extract_gyro_and_gravity(samples)
    t_imu = imu["t_s"]
    if len(t_imu) < 5 or float(t_imu[-1]) < 0.3:
        return None, 0.0

    # Initial offset from wall-clock difference if timestamps match
    vid_start_ms = parse_video_start_epoch_ms(Path(video_path))
    imu_start_ms = float(imu_session.get("started_at_ms") or 0.0)
    nominal_offset_s = 0.0
    if vid_start_ms is not None and imu_start_ms > 1e11:
        raw_diff_s = (vid_start_ms - imu_start_ms) / 1000.0
        # Keep only if recording started within +-30s of IMU log, otherwise assume 0..duration overlap
        if abs(raw_diff_s) <= float(t_imu[-1]) + 30.0:
            nominal_offset_s = float(np.clip(raw_diff_s, -8.0, float(t_imu[-1])))

    vis_t, vis_dps = _estimate_video_angular_speed_series(video_path)
    best_offset_s = nominal_offset_s
    if len(vis_t) >= 8 and float(np.std(vis_dps)) > 1.5 and float(np.std(imu["omega_norm"])) > 1.5:
        vis_z = (vis_dps - float(np.mean(vis_dps))) / max(1e-6, float(np.std(vis_dps)))
        best_corr = -1.0
        search_center = nominal_offset_s
        for delta in np.linspace(search_center - 5.0, search_center + 5.0, 201):
            t_q = vis_t + delta
            in_bounds = (t_q >= t_imu[0]) & (t_q <= t_imu[-1])
            if int(np.sum(in_bounds)) < 6:
                continue
            w_q = np.interp(t_q[in_bounds], t_imu, imu["omega_norm"])
            if float(np.std(w_q)) < 1e-3:
                continue
            w_z = (w_q - float(np.mean(w_q))) / float(np.std(w_q))
            corr = float(np.mean(vis_z[in_bounds] * w_z))
            if corr > best_corr:
                best_corr = corr
                best_offset_s = float(delta)
        if best_corr < 0.18:
            best_offset_s = nominal_offset_s

    return imu, round(best_offset_s, 3)


def interpolate_imu_at_times(
    imu: Dict[str, np.ndarray],
    times_s: List[float],
    time_offset_s: float = 0.0,
) -> List[Dict[str, Any]]:
    """Interpolates synced IMU attitude, gravity Up, angular rate, and steps at each keyframe."""
    t_imu = imu["t_s"]
    out: List[Dict[str, Any]] = []
    for t_vid in times_s:
        tq = float(np.clip(t_vid + time_offset_s, t_imu[0], t_imu[-1]))
        gx = float(np.interp(tq, t_imu, imu["accel_g"][:, 0]))
        gy = float(np.interp(tq, t_imu, imu["accel_g"][:, 1]))
        gz = float(np.interp(tq, t_imu, imu["accel_g"][:, 2]))
        pitch_d = float(np.interp(tq, t_imu, imu["pitch_deg"]))
        roll_d = float(np.interp(tq, t_imu, imu["roll_deg"]))

        # Compute camera Up unit vector in GL camera coords from pitch/roll (world +Y projected to camera)
        pr = math.radians(pitch_d)
        rr = math.radians(roll_d)
        cam_up_gl = np.array([
            -math.sin(rr) * math.cos(pr),
            math.cos(rr) * math.cos(pr),
            math.sin(pr),
        ], dtype=np.float64)
        cam_up_gl /= max(1e-9, float(np.linalg.norm(cam_up_gl)))

        out.append({
            "t_imu_s": round(tq, 3),
            "gyro_yaw_deg": round(float(np.interp(tq, t_imu, imu["gyro_yaw_deg"])), 2),
            "compass_yaw_deg": round(float(wrap_deg(np.interp(tq, t_imu, imu["compass_yaw_deg"]))), 2),
            "pitch_deg": round(pitch_d, 2),
            "roll_deg": round(roll_d, 2),
            "omega_norm_dps": round(float(np.interp(tq, t_imu, imu["omega_norm"])), 2),
            "steps_cum": round(float(np.interp(tq, t_imu, imu["steps"])), 2),
            "gravity_vec": [round(gx, 3), round(gy, 3), round(gz, 3)],
            "cam_up_gl": [round(float(x), 4) for x in cam_up_gl],
        })
    return out
