"""
WorldGit Spatial Hub — FastAPI + WebSocket backend for multi-wearer 3D memory,
Ctrl+F search, Spatial Triggers & Friend's World, Reality git diff, and Ctrl+Z undo.
"""
import asyncio
import json
import math
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pathlib import Path
from pydantic import BaseModel
import tempfile

from ai_services import (
    ai_status,
    analyze_frame_with_gemini,
    generate_ctrl_z_instructions,
    synthesize_elevenlabs_tts,
)
from db import SpatialDatabase
from imu_fusion import ImuSessionStore
from reconstruction import reconstructor

load_dotenv()

app = FastAPI(title="WorldGit Spatial Hub", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SpatialObject(BaseModel):
    id: str
    label: str
    position: List[float]  # [x, y, z] in meters
    seen_by: str
    timestamp: float
    confidence: float = 0.9


class SpatialPin(BaseModel):
    id: str
    author: str
    text: str
    pin_type: str = "note"  # "warning" | "note" | "friend_memory"
    position: List[float]
    landmark: str
    created_at: float
    age_label: str = "just now"


class WearerState(BaseModel):
    wearer_id: str
    name: str
    position: List[float]
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    compass_abs: Optional[float] = None
    steps: int = 0
    sensor_active: bool = False
    last_seen: float


class WorldSnapshot(BaseModel):
    snapshot_id: str
    label: str
    timestamp: float
    objects: List[SpatialObject]


def _generate_room_point_cloud() -> List[List[float]]:
    """Generate a procedural 3D workshop/room point cloud [x, y, z, r, g, b]."""
    pts: List[List[float]] = []

    # Floor grid points (subtle slate)
    for x_i in range(-10, 11):
        for z_i in range(-10, 11):
            x, z = x_i * 0.45, z_i * 0.45
            pts.append([round(x, 2), 0.0, round(z, 2), 0.16, 0.22, 0.31])

    # North & West walls
    for x_i in range(-18, 19):
        for y_i in range(1, 10):
            x = x_i * 0.25
            y = y_i * 0.25
            pts.append([round(x, 2), round(y, 2), -4.5, 0.28, 0.35, 0.48])

    for z_i in range(-18, 19):
        for y_i in range(1, 10):
            z = z_i * 0.25
            y = y_i * 0.25
            pts.append([-4.5, round(y, 2), round(z, 2), 0.24, 0.32, 0.44])

    # Workbench cluster near [-1.8, 0.8, -2.2]
    for x_i in range(-6, 7):
        for z_i in range(-3, 4):
            pts.append([round(-1.8 + x_i * 0.15, 2), 0.85, round(-2.2 + z_i * 0.15, 2), 0.55, 0.40, 0.28])

    # Pillar near [2.4, y, -1.0]
    for y_i in range(0, 12):
        for dx in (-0.2, 0.2):
            for dz in (-0.2, 0.2):
                pts.append([round(2.4 + dx, 2), round(y_i * 0.22, 2), round(-1.0 + dz, 2), 0.45, 0.52, 0.62])

    return pts


class WorldState:
    def __init__(self, db: SpatialDatabase) -> None:
        self.db = db
        self.wearers: Dict[str, WearerState] = {}
        self.objects: Dict[str, SpatialObject] = {}
        self.pins: Dict[str, SpatialPin] = {}
        self.snapshots: List[WorldSnapshot] = []
        self.points: List[List[float]] = _generate_room_point_cloud()
        self.wall_segments: List[List[float]] = []
        self.predicted_openings: List[List[float]] = []
        self.room_envelope: Optional[Dict[str, Any]] = None
        self.raw_point_count: int = len(self.points)
        self.completed_point_count: int = 0
        self.clients: List[WebSocket] = []
        self._seed_demo_world()

    def _seed_demo_world(self) -> None:
        now = time.time()

        # Pre-seed 3 crew wearers
        seed_wearers = [
            WearerState(wearer_id="wearer-a", name="Alex", position=[-2.0, 1.6, -1.5], yaw=45.0, last_seen=now),
            WearerState(wearer_id="wearer-b", name="Conor (You)", position=[1.5, 1.6, 2.5], yaw=0.0, last_seen=now),
            WearerState(wearer_id="wearer-c", name="Sam", position=[-3.5, 1.6, 1.0], yaw=120.0, last_seen=now),
        ]
        for w in seed_wearers:
            self.wearers[w.wearer_id] = w

        # Snapshot 1 (initial git commit)
        snap1_objects = [
            SpatialObject(
                id="obj-drill",
                label="Red DeWalt Drill",
                position=[-0.2, 0.9, -0.5],
                seen_by="Alex",
                timestamp=now - 900,
            ),
            SpatialObject(
                id="obj-socket",
                label="10mm Socket Wrench",
                position=[2.4, 0.8, -1.0],
                seen_by="Sam",
                timestamp=now - 900,
            ),
            SpatialObject(
                id="obj-keys",
                label="Car Keys",
                position=[-0.5, 0.75, 1.2],
                seen_by="Alex",
                timestamp=now - 900,
            ),
            SpatialObject(
                id="obj-medkit",
                label="First Aid Kit",
                position=[0.0, 1.4, -3.5],
                seen_by="Sam",
                timestamp=now - 900,
            ),
            SpatialObject(
                id="obj-battery",
                label="Backup Battery",
                position=[1.0, 0.8, 0.0],
                seen_by="Alex",
                timestamp=now - 900,
            ),
        ]
        snap1 = WorldSnapshot(
            snapshot_id="pass-1",
            label="pass-1-initial",
            timestamp=now - 900,
            objects=snap1_objects,
        )

        # Current objects (Pass 2: Drill moved, Backup Battery missing, Laser Level added)
        current_objects = [
            SpatialObject(
                id="obj-drill",
                label="Red DeWalt Drill",
                position=[-1.8, 0.9, -2.2],
                seen_by="Alex",
                timestamp=now - 120,
            ),
            SpatialObject(
                id="obj-socket",
                label="10mm Socket Wrench",
                position=[2.4, 0.8, -1.0],
                seen_by="Sam",
                timestamp=now - 180,
            ),
            SpatialObject(
                id="obj-keys",
                label="Car Keys",
                position=[-0.5, 0.75, 1.2],
                seen_by="Alex",
                timestamp=now - 60,
            ),
            SpatialObject(
                id="obj-medkit",
                label="First Aid Kit",
                position=[0.0, 1.4, -3.5],
                seen_by="Sam",
                timestamp=now - 240,
            ),
            SpatialObject(
                id="obj-laser",
                label="Laser Level",
                position=[3.0, 0.9, 0.5],
                seen_by="Conor (You)",
                timestamp=now - 30,
            ),
        ]
        for obj in current_objects:
            self.objects[obj.id] = obj
            self.db.upsert_object(obj.model_dump())

        snap2 = WorldSnapshot(
            snapshot_id="pass-2",
            label="pass-2-current",
            timestamp=now - 30,
            objects=[o.model_copy() for o in current_objects],
        )
        self.snapshots = [snap1, snap2]
        self.db.save_snapshot(snap1.model_dump())
        self.db.save_snapshot(snap2.model_dump())

        # Spatial Pins & Friend's World memories
        seed_pins = [
            SpatialPin(
                id="pin-1",
                author="Alex",
                text="LIVE 400V PANEL — Do not flip Breaker #4",
                pin_type="warning",
                position=[-2.2, 1.3, -1.8],
                landmark="Breaker Panel",
                created_at=now - 120,
                age_label="2m ago",
            ),
            SpatialPin(
                id="pin-2",
                author="Sam",
                text="Move these drywall boxes to Floor 2 before 18:00",
                pin_type="note",
                position=[1.2, 0.8, -2.0],
                landmark="Storage Rack",
                created_at=now - 840,
                age_label="14m ago",
            ),
            SpatialPin(
                id="pin-3",
                author="Sam",
                text="My favourite flat white in Helsinki — get the cardamom bun!",
                pin_type="friend_memory",
                position=[-1.0, 1.2, 2.0],
                landmark="Corner Espresso Bar",
                created_at=now - 63072000,
                age_label="2 years ago",
            ),
            SpatialPin(
                id="pin-4",
                author="Alex",
                text="Best late-night ramen spot from our NYC trip",
                pin_type="friend_memory",
                position=[2.8, 1.2, 1.8],
                landmark="East Village Ramen",
                created_at=now - 31536000,
                age_label="1 year ago",
            ),
        ]
        for pin in seed_pins:
            self.pins[pin.id] = pin
            self.db.upsert_pin(pin.model_dump())

    def take_snapshot(self, label: str) -> WorldSnapshot:
        snap = WorldSnapshot(
            snapshot_id=str(uuid.uuid4())[:8],
            label=label,
            timestamp=time.time(),
            objects=[obj.model_copy() for obj in self.objects.values()],
        )
        self.snapshots.append(snap)
        self.db.save_snapshot(snap.model_dump())
        return snap

    def compute_diff(self, from_idx: int = -2, to_idx: int = -1) -> Dict[str, Any]:
        if len(self.snapshots) < 2:
            return {"added": [], "removed": [], "moved": []}
        prev = {o.label: o for o in self.snapshots[from_idx].objects}
        curr = {o.label: o for o in self.snapshots[to_idx].objects}

        added = [curr[k].model_dump() for k in curr if k not in prev]
        removed = [prev[k].model_dump() for k in prev if k not in curr]
        moved = []
        for k in curr:
            if k in prev:
                p0, p1 = prev[k].position, curr[k].position
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(p0, p1)))
                if dist > 0.25:
                    moved.append(
                        {
                            "label": k,
                            "from": p0,
                            "to": p1,
                            "distance_m": round(dist, 2),
                        }
                    )
        return {"added": added, "removed": removed, "moved": moved}

    def serialize_state(self) -> Dict[str, Any]:
        return {
            "type": "world_state",
            "wearers": [w.model_dump() for w in self.wearers.values()],
            "objects": [o.model_dump() for o in self.objects.values()],
            "pins": [p.model_dump() for p in self.pins.values()],
            "snapshots": [s.model_dump() for s in self.snapshots],
            "diff": self.compute_diff(),
            "point_count": len(self.points),
            "raw_point_count": self.raw_point_count or len(self.points),
            "completed_point_count": self.completed_point_count,
            "wall_segments": self.wall_segments,
            "predicted_openings": self.predicted_openings,
            "room_envelope": self.room_envelope,
            "keyframes": reconstructor.keyframes[-36:],
            "tech_stack_status": {
                "aiven": self.db.status(),
                "ai": ai_status(),
            },
        }


db = SpatialDatabase()
world = WorldState(db=db)
world.objects.clear()

_STATIC_DIR = Path(__file__).resolve().parent / "static"
imu_store = ImuSessionStore(_STATIC_DIR / "imu_sessions")


def _load_oj_carton_into_world() -> int:
    pts_file = _STATIC_DIR / "oj_carton_points.json"
    if pts_file.exists():
        try:
            with open(pts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            world.points = data.get("points", [])
            world.raw_point_count = int(data.get("raw_point_count") or len(world.points))
            world.completed_point_count = int(data.get("completed_point_count") or 0)
            reconstructor.keyframes = data.get("keyframes", [])
            return len(world.points)
        except Exception:
            pass
    world.points = []
    world.raw_point_count = 0
    world.completed_point_count = 0
    return 0


def _load_startup_world() -> None:
    idx_path = _STATIC_DIR / "worlds" / "index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            wid = idx.get("active_world_id")
            worlds_list = idx.get("worlds", [])
            target = next((w for w in worlds_list if w.get("world_id") == wid), worlds_list[0] if worlds_list else None)
            if target and Path(target["json_path"]).exists():
                data = json.loads(Path(target["json_path"]).read_text(encoding="utf-8"))
                world.points = data.get("points", [])
                world.raw_point_count = int(data.get("raw_point_count") or len(world.points))
                world.completed_point_count = int(data.get("completed_point_count") or 0)
                world.wall_segments = data.get("wall_segments", [])
                world.predicted_openings = data.get("predicted_openings", [])
                world.room_envelope = data.get("room_envelope")
                reconstructor.keyframes = data.get("keyframes", [])
                return
        except Exception:
            pass
    _load_oj_carton_into_world()


_load_startup_world()


async def broadcast_state() -> None:
    payload = json.dumps(world.serialize_state())
    stale: List[WebSocket] = []
    for ws in world.clients:
        try:
            await ws.send_text(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        if ws in world.clients:
            world.clients.remove(ws)


async def broadcast_wearers() -> None:
    payload = json.dumps({
        "type": "wearers_update",
        "wearers": [w.model_dump() for w in world.wearers.values()],
    })
    stale: List[WebSocket] = []
    for ws in world.clients:
        try:
            await ws.send_text(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        if ws in world.clients:
            world.clients.remove(ws)


def _compute_directions(wearer: WearerState, target: SpatialObject) -> Dict[str, Any]:
    dx = target.position[0] - wearer.position[0]
    dz = target.position[2] - wearer.position[2]
    dist = round(math.sqrt(dx * dx + dz * dz), 1)
    angle_deg = round(math.degrees(math.atan2(dx, -dz)) - wearer.yaw) % 360

    if angle_deg < 25 or angle_deg > 335:
        turn = "straight ahead"
        arrow = "↑"
    elif angle_deg < 70:
        turn = "ahead-right"
        arrow = "↗"
    elif angle_deg < 115:
        turn = "to your right"
        arrow = "→"
    elif angle_deg < 180:
        turn = "behind-right"
        arrow = "↘"
    elif angle_deg < 245:
        turn = "behind-left"
        arrow = "↙"
    elif angle_deg < 290:
        turn = "to your left"
        arrow = "←"
    else:
        turn = "ahead-left"
        arrow = "↖"

    return {
        "distance_m": dist,
        "bearing_deg": angle_deg,
        "arrow": arrow,
        "turn": turn,
        "directions": f"{target.label} was last seen by {target.seen_by}. Walk {dist}m {turn}.",
    }


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "wearers": len(world.wearers),
        "objects": len(world.objects),
        "pins": len(world.pins),
        "points": len(world.points),
    }


@app.get("/api/state")
async def get_state() -> Dict[str, Any]:
    return world.serialize_state()


def _merge_points_voxel(
    existing: List[List[float]],
    incoming: List[List[float]],
    voxel_size: float = 0.025,
    max_points: int = 60000,
) -> List[List[float]]:
    """Merge 3D RGB points on a spatial voxel grid so repeated scans stay crisp and bounded."""
    grid: Dict[tuple, List[float]] = {}
    for pt in existing:
        if len(pt) < 6:
            continue
        key = (round(pt[0] / voxel_size), round(pt[1] / voxel_size), round(pt[2] / voxel_size))
        grid[key] = pt
    for pt in incoming:
        if len(pt) < 6:
            continue
        key = (round(pt[0] / voxel_size), round(pt[1] / voxel_size), round(pt[2] / voxel_size))
        grid[key] = [
            round(float(pt[0]), 3),
            round(float(pt[1]), 3),
            round(float(pt[2]), 3),
            round(float(pt[3]), 2),
            round(float(pt[4]), 2),
            round(float(pt[5]), 2),
        ]
    merged = list(grid.values())
    if len(merged) > max_points:
        merged = merged[-max_points:]
    return merged


@app.get("/api/points")
async def get_points() -> Dict[str, Any]:
    return {"point_count": len(world.points), "points": world.points}


@app.get("/api/points.bin")
async def get_points_bin() -> Response:
    """Streams [x, y, z, r, g, b] float32 tuples in compact binary for 60fps Three.js loading."""
    if not world.points:
        return Response(content=b"", media_type="application/octet-stream", headers={"X-Point-Count": "0"})
    arr = np.asarray(world.points, dtype="<f4")
    if arr.ndim != 2 or arr.shape[1] < 6:
        return Response(content=b"", media_type="application/octet-stream", headers={"X-Point-Count": "0"})
    arr = np.ascontiguousarray(arr[:, :6])
    return Response(
        content=arr.tobytes(),
        media_type="application/octet-stream",
        headers={"X-Point-Count": str(len(arr)), "Cache-Control": "no-store"},
    )


class ResetCloudRequest(BaseModel):
    empty: bool = True


@app.post("/api/reset-cloud")
async def reset_cloud(req: ResetCloudRequest) -> Dict[str, Any]:
    reconstructor.reset()
    if req.empty:
        world.points = []
        world.raw_point_count = 0
        world.completed_point_count = 0
        world.wall_segments = []
        world.predicted_openings = []
        world.room_envelope = None
        world.objects.clear()
    else:
        _load_oj_carton_into_world()
    await broadcast_state()
    return {"point_count": len(world.points), "empty": req.empty}


@app.post("/api/load-oj-model")
async def load_oj_model() -> Dict[str, Any]:
    count = _load_oj_carton_into_world()
    await broadcast_state()
    return {
        "ok": True,
        "point_count": count,
        "keyframes": reconstructor.keyframes,
        "glb_url": "/api/model.glb",
    }


@app.post("/api/load-vggt-model")
async def load_vggt_model() -> Dict[str, Any]:
    vggt_file = _STATIC_DIR / "vggt_walkthrough_points.json"
    if not vggt_file.exists():
        return {"ok": False, "error": "No VGGT walkthrough point cloud generated yet"}
    with open(vggt_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    world.points = data.get("points", [])
    world.raw_point_count = int(data.get("raw_point_count") or len(world.points))
    world.completed_point_count = int(data.get("completed_point_count") or 0)
    reconstructor.keyframes = data.get("keyframes", [])
    segs = data.get("wall_segments")
    if segs is None and world.points:
        from walkthrough_vggt import predict_wall_segments
        pts_np = np.asarray(world.points, dtype=np.float64)[:, :3]
        segs = predict_wall_segments(pts_np, reconstructor.keyframes)
    world.wall_segments = segs or []
    world.predicted_openings = data.get("predicted_openings", [])
    world.room_envelope = data.get("room_envelope")
    await broadcast_state()
    return {
        "ok": True,
        "point_count": len(world.points),
        "raw_point_count": world.raw_point_count,
        "completed_point_count": world.completed_point_count,
        "wall_segments": world.wall_segments,
        "predicted_openings": world.predicted_openings,
        "room_envelope": world.room_envelope,
        "keyframes": reconstructor.keyframes,
        "elapsed_s": data.get("elapsed_s"),
        "device": data.get("device"),
    }


_vggt_runner = None
_multi_world_mgr = None


def _get_vggt_runner():
    global _vggt_runner
    if _vggt_runner is None:
        from walkthrough_vggt import IncrementalVGGTReconstructor
        _vggt_runner = IncrementalVGGTReconstructor()
    return _vggt_runner


def _get_multi_world_mgr():
    global _multi_world_mgr
    if _multi_world_mgr is None:
        from multi_world import MultiWorldManager
        _multi_world_mgr = MultiWorldManager(
            runner=_get_vggt_runner(),
            worlds_root=_STATIC_DIR / "worlds",
        )
    return _multi_world_mgr


def _load_world_json(json_path: Path) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    world.points = data.get("points", [])
    world.raw_point_count = int(data.get("raw_point_count") or len(world.points))
    world.completed_point_count = int(data.get("completed_point_count") or 0)
    world.wall_segments = data.get("wall_segments", [])
    world.predicted_openings = data.get("predicted_openings", [])
    world.room_envelope = data.get("room_envelope")
    reconstructor.keyframes = data.get("keyframes", [])
    return data


@app.get("/api/worlds")
async def list_worlds() -> Dict[str, Any]:
    idx_path = _STATIC_DIR / "worlds" / "index.json"
    if not idx_path.exists():
        return {"worlds": [], "clips": {}, "edges": [], "active_world_id": None}
    return json.loads(idx_path.read_text(encoding="utf-8"))


@app.post("/api/worlds/{world_id}/activate")
async def activate_world(world_id: str) -> Dict[str, Any]:
    idx_path = _STATIC_DIR / "worlds" / "index.json"
    if not idx_path.exists():
        return {"ok": False, "error": "No worlds index built yet"}
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    target = next((w for w in idx.get("worlds", []) if w.get("world_id") == world_id), None)
    if target is None:
        return {"ok": False, "error": f"Unknown world_id: {world_id}"}
    jpath = Path(target["json_path"])
    if not jpath.exists():
        return {"ok": False, "error": f"World file missing: {jpath}"}
    _load_world_json(jpath)
    idx["active_world_id"] = world_id
    idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")
    await broadcast_state()
    return {
        "ok": True,
        "world_id": world_id,
        "label": target.get("label"),
        "status": target.get("status"),
        "clips": target.get("clips", []),
        "point_count": len(world.points),
        "raw_point_count": world.raw_point_count,
        "completed_point_count": world.completed_point_count,
        "wall_segments": world.wall_segments,
        "predicted_openings": world.predicted_openings,
        "room_envelope": world.room_envelope,
        "keyframes": reconstructor.keyframes,
    }


class ImuSessionUpload(BaseModel):
    session_id: Optional[str] = None
    device: str = "meta-ray-ban-display"
    started_at_ms: Optional[float] = None
    ended_at_ms: Optional[float] = None
    step_count: int = 0
    samples: List[Dict[str, Any]] = []


@app.post("/api/imu-sessions")
async def upload_imu_session(payload: ImuSessionUpload) -> Dict[str, Any]:
    """Stores a timestamped 9-axis IMU session captured on the Meta Ray-Ban Display glasses."""
    summary = imu_store.save_session(payload.model_dump())
    return {"ok": True, "session": summary}


@app.get("/api/imu-sessions")
async def list_imu_sessions() -> Dict[str, Any]:
    return {"sessions": imu_store.list_sessions()}


@app.get("/api/imu-sessions/latest")
async def latest_imu_session(include_samples: bool = False) -> Dict[str, Any]:
    sessions = imu_store.list_sessions()
    if not sessions:
        return {"session": None, "sessions_count": 0}
    latest = imu_store.load_session(sessions[0]["session_id"]) if include_samples else sessions[0]
    return {"session": latest, "sessions_count": len(sessions)}


def _probe_video_duration_s(video_path: Path) -> float:
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        total = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        cap.release()
        return total / max(1.0, fps)
    except Exception:
        return 0.0


async def _background_ingest_dat_scan(saved_vid: Path, imu_session_id: str) -> None:
    try:
        vid_dur_s = _probe_video_duration_s(saved_vid)
        imu_sess = imu_store.match_video_session(saved_vid, vid_dur_s, explicit_session_id=imu_session_id)
        mgr = _get_multi_world_mgr()
        clip_meta = await asyncio.to_thread(mgr.ingest_video, saved_vid, None, 24, 260000, True, imu_sess)
        idx = await asyncio.to_thread(mgr.reconcile_worlds, 320000)
        cid = clip_meta["clip_id"]
        containing = next((w for w in idx.get("worlds", []) if cid in w.get("clips", [])), None)
        if containing:
            _load_world_json(Path(containing["json_path"]))
            idx["active_world_id"] = containing["world_id"]
            mgr.save_index(idx)
            await broadcast_state()
    except Exception as exc:
        print(f"[dat-scans] background ingest error: {exc}")


@app.post("/api/dat-scans")
async def upload_dat_scan(
    request: Request,
    name: str = "scan.mp4",
    imu_session_id: str = "auto",
    started_at_ms: Optional[float] = None,
    reconstruct: bool = True,
) -> Dict[str, Any]:
    """Saves a synchronized .mp4 + 9-axis IMU scan from the WorldGit Android DAT companion app and queues 3D reconstruction."""
    raw_bytes = await request.body()
    if not raw_bytes:
        return {"ok": False, "error": "Empty video upload"}

    safe_name = Path(name).name or f"scan_{int(time.time())}.mp4"
    scans_dir = Path(__file__).parent / "static" / "dat_scans"
    scans_dir.mkdir(parents=True, exist_ok=True)
    saved_vid = scans_dir / safe_name
    saved_vid.write_bytes(raw_bytes)

    vid_dur_s = _probe_video_duration_s(saved_vid)
    imu_sess = imu_store.match_video_session(saved_vid, vid_dur_s, explicit_session_id=imu_session_id)

    if reconstruct:
        asyncio.create_task(_background_ingest_dat_scan(saved_vid, imu_session_id))

    return {
        "ok": True,
        "video_name": safe_name,
        "video_bytes": len(raw_bytes),
        "video_duration_s": round(vid_dur_s, 2),
        "saved_path": str(saved_vid),
        "reconstruct_queued": reconstruct,
        "imu_matched": (
            {
                "session_id": imu_sess.get("session_id"),
                "device": imu_sess.get("device"),
                "sample_count": imu_sess.get("sample_count"),
                "step_count": imu_sess.get("step_count"),
            }
            if imu_sess
            else None
        ),
    }


@app.get("/api/dat-scans")
async def list_dat_scans() -> Dict[str, Any]:
    scans_dir = Path(__file__).parent / "static" / "dat_scans"
    scans_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(scans_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        items.append(
            {
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "mtime": p.stat().st_mtime,
                "url": f"/static/dat_scans/{p.name}",
            }
        )
    return {"scans": items}


@app.api_route("/api/download/worldgit-dat.apk", methods=["GET", "HEAD"])
async def download_dat_apk() -> Response:
    apk_path = Path(__file__).parent / "static" / "worldgit-dat.apk"
    if not apk_path.exists():
        return Response(status_code=404, content=b"APK not built yet")
    return FileResponse(
        path=str(apk_path),
        media_type="application/vnd.android.package-archive",
        filename="worldgit-glasses.apk",
    )


@app.post("/api/worlds/add-video")
async def add_video_to_worlds(
    request: Request,
    name: str = "upload.mp4",
    imu_session_id: str = "auto",
) -> Dict[str, Any]:
    """Reconstructs an uploaded .mp4 clip, discovers intersections, and splices into matching worlds."""
    raw_bytes = await request.body()
    if not raw_bytes:
        return {"ok": False, "error": "Empty video upload"}

    safe_name = Path(name).name or f"clip_{int(time.time())}.mp4"
    tmp_dir = Path(tempfile.gettempdir()) / "worldgit_uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_vid = tmp_dir / safe_name
    tmp_vid.write_bytes(raw_bytes)

    try:
        vid_dur_s = _probe_video_duration_s(tmp_vid)
        imu_sess = imu_store.match_video_session(tmp_vid, vid_dur_s, explicit_session_id=imu_session_id)
        mgr = _get_multi_world_mgr()
        clip_meta = await asyncio.to_thread(mgr.ingest_video, tmp_vid, None, 24, 260000, True, imu_sess)
        idx = await asyncio.to_thread(mgr.reconcile_worlds, 320000)
        cid = clip_meta["clip_id"]
        containing = next((w for w in idx.get("worlds", []) if cid in w.get("clips", [])), None)
        if containing:
            _load_world_json(Path(containing["json_path"]))
            idx["active_world_id"] = containing["world_id"]
            mgr.save_index(idx)
            await broadcast_state()
        return {
            "ok": True,
            "clip": clip_meta,
            "imu_matched": (
                {
                    "session_id": imu_sess.get("session_id"),
                    "sample_count": imu_sess.get("sample_count"),
                    "step_count": imu_sess.get("step_count"),
                }
                if imu_sess
                else None
            ),
            "active_world": containing,
            "worlds": idx.get("worlds", []),
            "edges": idx.get("edges", []),
            "point_count": len(world.points),
            "raw_point_count": world.raw_point_count,
            "completed_point_count": world.completed_point_count,
            "wall_segments": world.wall_segments,
            "predicted_openings": world.predicted_openings,
            "room_envelope": world.room_envelope,
            "keyframes": reconstructor.keyframes,
        }
    finally:
        if tmp_vid.exists():
            tmp_vid.unlink()


@app.post("/api/reconstruct-video-bytes")
async def reconstruct_video_bytes(request: Request) -> Dict[str, Any]:
    """Reconstructs any uploaded .mp4 walkthrough video incrementally via VGGT-1B."""
    raw_bytes = await request.body()
    if not raw_bytes:
        return {"ok": False, "error": "Empty video upload"}

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(raw_bytes)
        tmp_vid = tf.name

    try:
        out_json = _STATIC_DIR / "vggt_walkthrough_points.json"
        runner = _get_vggt_runner()
        loop = asyncio.get_running_loop()

        def _on_window(msg: str, pts_list: List[List[float]], kfs: List[Dict[str, Any]]) -> None:
            world.points = pts_list
            reconstructor.keyframes = kfs
            asyncio.run_coroutine_threadsafe(broadcast_state(), loop)

        payload = await asyncio.to_thread(
            runner.reconstruct_video_incremental,
            tmp_vid,
            out_json,
            8,
            3,
            24,
            260000,
            48.0,
            _on_window,
        )
        world.points = payload.get("points", [])
        world.raw_point_count = int(payload.get("raw_point_count") or len(world.points))
        world.completed_point_count = int(payload.get("completed_point_count") or 0)
        world.wall_segments = payload.get("wall_segments", [])
        world.predicted_openings = payload.get("predicted_openings", [])
        world.room_envelope = payload.get("room_envelope")
        reconstructor.keyframes = payload.get("keyframes", [])
        await broadcast_state()
        return {
            "ok": True,
            "point_count": len(world.points),
            "raw_point_count": world.raw_point_count,
            "completed_point_count": world.completed_point_count,
            "wall_segments": world.wall_segments,
            "predicted_openings": world.predicted_openings,
            "room_envelope": world.room_envelope,
            "keyframes": reconstructor.keyframes,
            "elapsed_s": payload.get("elapsed_s"),
            "device": payload.get("device"),
        }
    finally:
        if os.path.exists(tmp_vid):
            os.remove(tmp_vid)


@app.get("/api/model.glb")
async def get_model_glb() -> FileResponse:
    return FileResponse(
        path=str(_STATIC_DIR / "oj_carton.glb"),
        media_type="model/gltf-binary",
        filename="bonne_orange_juice_3d.glb",
    )


@app.get("/api/model.ply")
async def get_model_ply() -> FileResponse:
    return FileResponse(
        path=str(_STATIC_DIR / "oj_carton.ply"),
        media_type="application/octet-stream",
        filename="bonne_orange_juice_3d.ply",
    )


@app.get("/api/aiven-status")
async def get_aiven_status() -> Dict[str, Any]:
    return db.status()


class CtrlFQuery(BaseModel):
    wearer_id: str = "wearer-b"
    query: str


@app.post("/api/ctrl-f")
async def ctrl_f_search(req: CtrlFQuery) -> Dict[str, Any]:
    q = req.query.lower().strip()
    matches = [o for o in world.objects.values() if q in o.label.lower()]
    if not matches:
        # Token fallback
        tokens = [t for t in q.split() if len(t) > 2]
        matches = [
            o for o in world.objects.values() if any(t in o.label.lower() for t in tokens)
        ]
    if not matches:
        return {"found": False, "message": f"No teammate has seen '{req.query}' yet."}

    target = matches[0]
    wearer = world.wearers.get(req.wearer_id)
    if not wearer:
        return {
            "found": True,
            "object": target.model_dump(),
            "directions": f"{target.seen_by} saw {target.label}.",
        }

    nav = _compute_directions(wearer, target)
    return {
        "found": True,
        "object": target.model_dump(),
        "path": [wearer.position, target.position],
        **nav,
    }


@app.post("/api/snapshot")
async def create_snapshot(label: str = "checkpoint") -> Dict[str, Any]:
    snap = world.take_snapshot(label)
    await broadcast_state()
    return snap.model_dump()


@app.get("/api/diff")
async def get_diff() -> Dict[str, Any]:
    return world.compute_diff()


@app.post("/api/ctrl-z")
async def ctrl_z_undo() -> Dict[str, Any]:
    diff = world.compute_diff()
    ai_undo = await generate_ctrl_z_instructions(diff)
    return {
        "diff": diff,
        "provider": ai_undo["provider"],
        "undo_steps": ai_undo["undo_steps"],
    }


@app.post("/api/pin")
async def add_pin(pin: SpatialPin) -> Dict[str, Any]:
    world.pins[pin.id] = pin
    db.upsert_pin(pin.model_dump())
    await broadcast_state()
    return pin.model_dump()


@app.post("/api/object")
async def upsert_object(obj: SpatialObject) -> Dict[str, Any]:
    world.objects[obj.id] = obj
    db.upsert_object(obj.model_dump())
    await broadcast_state()
    return obj.model_dump()


class FrameAnalysisRequest(BaseModel):
    wearer_id: str = "wearer-b"
    image_b64: str = ""
    pose: Optional[List[float]] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None
    compass_abs: Optional[float] = None
    steps: Optional[int] = None
    points: Optional[List[List[float]]] = None
    replace_cloud: bool = False
    skip_ai: bool = True
    scan_mode: str = "surround"
    bg_cutoff: float = 0.22


@app.post("/api/analyze-frame")
async def analyze_frame(req: FrameAnalysisRequest) -> Dict[str, Any]:
    wearer = world.wearers.get(req.wearer_id)
    pose = req.pose or (wearer.position if wearer else [0.0, 1.5, 0.0])
    yaw = req.yaw if req.yaw is not None else (wearer.yaw if wearer else 0.0)

    if wearer:
        if req.pose is not None:
            wearer.position = [round(float(v), 2) for v in req.pose[:3]]
        if req.yaw is not None:
            wearer.yaw = round(float(req.yaw), 1)
        if req.pitch is not None:
            wearer.pitch = round(float(req.pitch), 1)
        if req.roll is not None:
            wearer.roll = round(float(req.roll), 1)
        if req.compass_abs is not None:
            wearer.compass_abs = round(float(req.compass_abs), 1)
        if req.steps is not None:
            wearer.steps = int(req.steps)
        wearer.last_seen = time.time()

    if req.replace_cloud:
        world.points = []
        reconstructor.reset()

    # 1. Pure 3D Surround / Room Reconstruction (Indexing disabled)
    modal_url = os.getenv("MODAL_RECONSTRUCT_URL")
    added_points = 0
    modal_provider = "local-synthetic-patch"
    recon_meta: Dict[str, Any] = {}
    created_objects: List[Dict[str, Any]] = []
    now = time.time()

    if req.image_b64:
        recon_res = reconstructor.process_keyframe(
            req.image_b64,
            hint_pose=req.pose,
            hint_yaw=req.yaw,
            hint_pitch=req.pitch,
            mode=req.scan_mode,
            bg_cutoff=req.bg_cutoff,
            run_detection=False,
        )
        if recon_res.get("ok"):
            pts = recon_res.get("points", [])
            if req.scan_mode == "surround":
                world.points = reconstructor.carve_and_fuse_points(world.points, pts, voxel_size=0.016)
            else:
                world.points = _merge_points_voxel(world.points, pts, voxel_size=0.045)
            added_points = len(pts)
            modal_provider = recon_res.get("provider", "depth-anything-v2-surround")
            pose = recon_res["pose"]
            yaw = recon_res["yaw"]
            if wearer:
                wearer.position = pose
                wearer.yaw = yaw
                wearer.pitch = recon_res["pitch"]
                wearer.last_seen = now
            recon_meta = {
                "pose": pose,
                "yaw": yaw,
                "pitch": recon_res["pitch"],
                "inliers": recon_res.get("inliers", 0),
                "keyframe_count": recon_res.get("keyframe_count", 0),
                "keyframes": recon_res.get("keyframes", []),
                "depth_preview_b64": recon_res.get("depth_preview_b64", ""),
                "detections_3d": [],
            }

    if added_points == 0 and req.points:
        world.points = _merge_points_voxel(world.points, req.points)
        added_points = len(req.points)
        modal_provider = "laptop-rgb-depth"
    elif added_points == 0 and modal_url and req.image_b64:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    modal_url,
                    json={"image_b64": req.image_b64, "pose": pose, "yaw": yaw},
                )
                if r.status_code == 200:
                    data = r.json()
                    pts = data.get("points", [])
                    world.points = _merge_points_voxel(world.points, pts)
                    added_points = len(pts)
                    modal_provider = "modal-gpu-l4"
        except Exception:
            pass

    if added_points == 0 and not req.image_b64:
        # Add a local depth-projected point patch only when synthetic POV (no camera image) is used
        rad = math.radians(yaw)
        patch: List[List[float]] = []
        for dx_i in range(-3, 4):
            for dy_i in range(-2, 3):
                dist = 1.8
                xw = pose[0] + dx_i * 0.2 * math.cos(rad) + dist * math.sin(rad)
                yw = max(0.2, pose[1] + dy_i * 0.2)
                zw = pose[2] - dx_i * 0.2 * math.sin(rad) - dist * math.cos(rad)
                patch.append([round(xw, 2), round(yw, 2), round(zw, 2), 0.22, 0.74, 0.97])
        world.points = _merge_points_voxel(world.points, patch)
        added_points = len(patch)

    # 2. Optional Gemini semantic object indexing when live GEMINI_API_KEY is configured or no image_b64
    if req.skip_ai or (req.image_b64 and not os.getenv("GEMINI_API_KEY")):
        analysis = {"provider": modal_provider, "detections": []}
    else:
        analysis = await analyze_frame_with_gemini(req.image_b64, req.wearer_id, pose)
    rad = math.radians(yaw)

    for det in analysis["detections"]:
        rx = float(det.get("rel_x", 0.0))
        ry = float(det.get("rel_y", -0.3))
        rz = float(det.get("rel_z", -1.5))
        xw = round(pose[0] + rx * math.cos(rad) - rz * math.sin(rad), 2)
        yw = round(max(0.2, pose[1] + ry), 2)
        zw = round(pose[2] - rx * math.sin(rad) + rz * math.cos(rad), 2)

        label = str(det.get("label", "Detected Item"))
        obj_id = f"obj-{label.lower().replace(' ', '-')[:20]}"
        obj = SpatialObject(
            id=obj_id,
            label=label,
            position=[xw, yw, zw],
            seen_by=wearer.name if wearer else req.wearer_id,
            timestamp=now,
            confidence=float(det.get("confidence", 0.9)),
        )
        world.objects[obj.id] = obj
        db.upsert_object(obj.model_dump())
        created_objects.append(obj.model_dump())

    await broadcast_state()
    return {
        "ai_provider": analysis["provider"],
        "modal_provider": modal_provider,
        "added_points": added_points,
        "detected_objects": created_objects,
        "total_points": len(world.points),
        **recon_meta,
    }


class TtsRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def tts_endpoint(req: TtsRequest) -> Dict[str, Any]:
    return await synthesize_elevenlabs_tts(req.text)


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    world.clients.append(websocket)
    await websocket.send_text(json.dumps(world.serialize_state()))
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "pose":
                w = WearerState(
                    wearer_id=msg["wearer_id"],
                    name=msg.get("name", msg["wearer_id"]),
                    position=msg["position"],
                    yaw=float(msg.get("yaw", 0.0)),
                    pitch=float(msg.get("pitch", 0.0)),
                    roll=float(msg.get("roll", 0.0)),
                    compass_abs=(
                        float(msg["compass_abs"]) if msg.get("compass_abs") is not None else None
                    ),
                    steps=int(msg.get("steps", 0)),
                    sensor_active=bool(msg.get("sensor_active", False)),
                    last_seen=time.time(),
                )
                world.wearers[w.wearer_id] = w
                await broadcast_wearers()

            elif msg_type == "frame":
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "frame_ack",
                            "wearer_id": msg.get("wearer_id"),
                            "ts": time.time(),
                        }
                    )
                )
    except WebSocketDisconnect:
        if websocket in world.clients:
            world.clients.remove(websocket)
