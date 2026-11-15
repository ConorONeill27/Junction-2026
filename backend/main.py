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
    answer_space_question_with_gemini,
    generate_ctrl_z_instructions,
    synthesize_elevenlabs_tts,
    transcribe_pcm_with_gemini,
)
from db import SpatialDatabase
from imu_fusion import ImuSessionStore
from reconstruction import reconstructor
from social_store import (
    USER_TO_WEARER,
    WEARER_TO_USER,
    build_ask_answer,
    can_user_view,
    evaluate_sighting_transition,
    resolve_viewer_id,
    resolve_zone,
    seed_everyday_social_data,
)
from auto_indexer import (
    auto_index_from_photos,
    auto_index_from_video,
    persist_indexed_world,
)
from video_localizer import DEFAULT_ROOM_PHOTOS_DIR, DEFAULT_VIDEO_PATH, localize_video_on_world

load_dotenv()

app = FastAPI(title="WorldGit Spatial Hub", version="0.3.0")

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
    world_id: str = "world_1"
    zone_name: str = ""
    owner_id: str = "usr-conor"
    audience_type: str = "group"
    group_ids: List[str] = ["grp-room"]


class SpatialPin(BaseModel):
    id: Optional[str] = None
    author: str
    text: str
    pin_type: str = "note"  # "warning" | "note" | "friend_memory" | "bottle"
    position: List[float]
    landmark: str
    created_at: Optional[float] = None
    age_label: str = "just now"
    world_id: Optional[str] = None
    author_id: Optional[str] = None
    audience_type: str = "public"
    group_ids: List[str] = []
    target_user_ids: List[str] = []
    rating: Optional[int] = None
    place_name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


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
    user_id: Optional[str] = None
    avatar_color: Optional[str] = None
    world_id: Optional[str] = None


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
        self.video_trail: List[Dict[str, Any]] = []
        self.active_world_id: str = "world_1"
        self.world_cache: Dict[str, Dict[str, Any]] = {}
        self.scan_jobs: Dict[str, Dict[str, Any]] = {}
        self.clients: Dict[WebSocket, Dict[str, Optional[str]]] = {}
        self._seed_demo_world()

    def _seed_demo_world(self) -> None:
        now = time.time()
        self.db.migrate_purge_legacy_industrial_seed()
        seed_everyday_social_data(self.db)

        seed_wearers = [
            WearerState(
                wearer_id="wearer-b",
                name="Conor",
                user_id="usr-conor",
                avatar_color="#2997ff",
                position=[0.95, 1.4, 0.35],
                yaw=260.0,
                world_id="world_1",
                last_seen=now,
            ),
            WearerState(
                wearer_id="wearer-a",
                name="Alec",
                user_id="usr-alec",
                avatar_color="#34d399",
                position=[0.1, 1.4, -1.3],
                yaw=45.0,
                world_id="world_1",
                last_seen=now,
            ),
        ]
        for w in seed_wearers:
            self.wearers[w.wearer_id] = w

        # Hydrate real persisted objects and pins from DB
        for raw_obj in self.db.list_objects():
            self.objects[raw_obj["id"]] = SpatialObject(**raw_obj)
        for raw_pin in self.db.list_pins():
            self.pins[raw_pin["id"]] = SpatialPin(**raw_pin)

        # Preserve empty-DB snapshot baseline for Ctrl+Z compatibility
        snap = WorldSnapshot(
            snapshot_id="pass-initial",
            label="initial-space",
            timestamp=now - 60,
            objects=[o.model_copy() for o in self.objects.values()],
        )
        self.snapshots = [snap]
        self.db.save_snapshot(snap.model_dump())

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

    def get_world_payload(self, world_id: Optional[str] = None) -> Dict[str, Any]:
        wid = world_id or self.active_world_id
        if wid in self.world_cache:
            return self.world_cache[wid]
        if wid != self.active_world_id:
            idx_path = _STATIC_DIR / "worlds" / "index.json"
            if idx_path.exists():
                try:
                    idx = json.loads(idx_path.read_text(encoding="utf-8"))
                    target = next((w for w in idx.get("worlds", []) if w.get("world_id") == wid), None)
                    if target and Path(target["json_path"]).exists():
                        data = json.loads(Path(target["json_path"]).read_text(encoding="utf-8"))
                        cached = {
                            "points": data.get("points", []),
                            "raw_point_count": int(data.get("raw_point_count") or len(data.get("points", []))),
                            "completed_point_count": int(data.get("completed_point_count") or 0),
                            "wall_segments": data.get("wall_segments", []),
                            "predicted_openings": data.get("predicted_openings", []),
                            "room_envelope": data.get("room_envelope"),
                            "keyframes": data.get("keyframes", []),
                        }
                        self.world_cache[wid] = cached
                        return cached
                except Exception:
                    pass
        return {
            "points": self.points,
            "raw_point_count": self.raw_point_count or len(self.points),
            "completed_point_count": self.completed_point_count,
            "wall_segments": self.wall_segments,
            "predicted_openings": self.predicted_openings,
            "room_envelope": self.room_envelope,
            "keyframes": reconstructor.keyframes[-120:],
        }

    def serialize_state(
        self,
        viewer_id: Optional[str] = None,
        world_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        vid = resolve_viewer_id(viewer_id)
        wid = world_id or self.active_world_id
        wp = self.get_world_payload(wid)

        visible_objects = [
            o.model_dump()
            for o in self.objects.values()
            if can_user_view(o.model_dump(), vid, self.db)
        ]
        visible_obj_ids = {o["id"] for o in visible_objects}

        visible_pins = [
            p.model_dump()
            for p in self.pins.values()
            if can_user_view(p.model_dump(), vid, self.db)
        ]
        visible_events = [
            ev
            for ev in self.db.list_object_events(world_id=wid, limit=80)
            if ev["object_id"] in visible_obj_ids or vid == ev.get("actor_user_id")
        ]
        spaces_meta = [
            s for s in self.db.list_spaces_meta() if can_user_view(s, vid, self.db)
        ]

        return {
            "type": "world_state",
            "viewer_id": vid,
            "world_id": wid,
            "wearers": [w.model_dump() for w in self.wearers.values()],
            "objects": visible_objects,
            "pins": visible_pins,
            "zones": self.db.list_zones(world_id=wid),
            "object_events": visible_events,
            "spaces": spaces_meta,
            "scan_jobs": list(self.scan_jobs.values()),
            "snapshots": [s.model_dump() for s in self.snapshots],
            "diff": self.compute_diff(),
            "point_count": len(wp["points"]),
            "raw_point_count": wp["raw_point_count"],
            "completed_point_count": wp["completed_point_count"],
            "wall_segments": wp["wall_segments"],
            "predicted_openings": wp["predicted_openings"],
            "room_envelope": wp["room_envelope"],
            "keyframes": wp["keyframes"][-120:],
            "video_trail": self.video_trail,
            "tech_stack_status": {
                "aiven": self.db.status(),
                "ai": ai_status(),
            },
        }


db = SpatialDatabase()
world = WorldState(db=db)

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
                world.active_world_id = target.get("world_id") or "world_1"
                world.points = data.get("points", [])
                world.raw_point_count = int(data.get("raw_point_count") or len(world.points))
                world.completed_point_count = int(data.get("completed_point_count") or 0)
                world.wall_segments = data.get("wall_segments", [])
                world.predicted_openings = data.get("predicted_openings", [])
                world.room_envelope = data.get("room_envelope")
                reconstructor.keyframes = data.get("keyframes", [])
                world.world_cache[world.active_world_id] = {
                    "points": world.points,
                    "raw_point_count": world.raw_point_count,
                    "completed_point_count": world.completed_point_count,
                    "wall_segments": world.wall_segments,
                    "predicted_openings": world.predicted_openings,
                    "room_envelope": world.room_envelope,
                    "keyframes": reconstructor.keyframes,
                }
                return
        except Exception:
            pass
    _load_oj_carton_into_world()


_load_startup_world()


async def broadcast_state() -> None:
    stale: List[WebSocket] = []
    for ws, ctx in list(world.clients.items()):
        try:
            payload = json.dumps(world.serialize_state(viewer_id=ctx.get("viewer_id"), world_id=ctx.get("world_id")))
            await ws.send_text(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        world.clients.pop(ws, None)


async def broadcast_wearers() -> None:
    payload = json.dumps({
        "type": "wearers_update",
        "wearers": [w.model_dump() for w in world.wearers.values()],
    })
    stale: List[WebSocket] = []
    for ws in list(world.clients.keys()):
        try:
            await ws.send_text(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        world.clients.pop(ws, None)


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
async def get_state(viewer_id: Optional[str] = None, world_id: Optional[str] = None) -> Dict[str, Any]:
    return world.serialize_state(viewer_id=viewer_id, world_id=world_id)


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
async def get_points_bin(world_id: Optional[str] = None) -> Response:
    """Streams [x, y, z, r, g, b] float32 tuples in compact binary for 60fps Three.js loading."""
    pts = world.get_world_payload(world_id)["points"] if world_id else world.points
    if not pts:
        return Response(content=b"", media_type="application/octet-stream", headers={"X-Point-Count": "0"})
    arr = np.asarray(pts, dtype="<f4")
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
    job_id = f"scan-{uuid.uuid4().hex[:8]}"
    world.scan_jobs[job_id] = {
        "job_id": job_id,
        "name": saved_vid.name,
        "status": "processing",
        "started_at": time.time(),
    }
    await broadcast_state()
    try:
        vid_dur_s = _probe_video_duration_s(saved_vid)
        imu_sess = imu_store.match_video_session(saved_vid, vid_dur_s, explicit_session_id=imu_session_id)
        mgr = _get_multi_world_mgr()
        clip_meta = await asyncio.to_thread(mgr.ingest_video, saved_vid, None, 24, 260000, True, imu_sess)
        idx = await asyncio.to_thread(mgr.reconcile_worlds, 320000)
        cid = clip_meta["clip_id"]
        containing = next((w for w in idx.get("worlds", []) if cid in w.get("clips", [])), None)
        if containing:
            wid = containing["world_id"]
            _load_world_json(Path(containing["json_path"]))
            world.active_world_id = wid
            idx["active_world_id"] = wid
            mgr.save_index(idx)
            db.upsert_space_meta({
                "world_id": wid,
                "display_name": containing.get("label") or saved_vid.stem,
                "owner_id": "usr-conor",
                "audience_type": "group",
                "group_ids": ["grp-room"],
            })
            _iz, _io = await asyncio.to_thread(
                auto_index_from_video,
                list(world.points),
                list(reconstructor.keyframes),
                saved_vid,
                1,
                True,
            )
            if _iz or _io:
                _apply_auto_index_result(wid, _iz, _io, actor_user_id="usr-conor")
            world.scan_jobs[job_id]["status"] = "ready"
            world.scan_jobs[job_id]["world_id"] = wid
            await broadcast_state()
    except Exception as exc:
        world.scan_jobs[job_id]["status"] = "failed"
        world.scan_jobs[job_id]["error"] = str(exc)
        print(f"[dat-scans] background ingest error: {exc}")
        await broadcast_state()


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
            wid = containing["world_id"]
            _load_world_json(Path(containing["json_path"]))
            idx["active_world_id"] = wid
            mgr.save_index(idx)
            _iz, _io = await asyncio.to_thread(
                auto_index_from_video,
                list(world.points),
                list(reconstructor.keyframes),
                tmp_vid,
                1,
                True,
            )
            if _iz or _io:
                _apply_auto_index_result(wid, _iz, _io, actor_user_id="usr-conor")
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


@app.post("/api/localize-video")
async def localize_video_endpoint(request: Request) -> Dict[str, Any]:
    """Localize a video (uploaded .mp4 or captured hotel clip) onto the active 3D room map."""
    tmp_vid: Optional[str] = None
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        body = await request.body()
        video_path: Optional[Path] = None
        if body and ("video/" in ctype or "application/octet-stream" in ctype or "multipart/form-data" in ctype):
            if "multipart/form-data" in ctype:
                form = await request.form()
                file_item = form.get("file") or form.get("video")
                if file_item is not None and hasattr(file_item, "read"):
                    data = await file_item.read()
                    if data:
                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                            tf.write(data)
                            tmp_vid = tf.name
                        video_path = Path(tmp_vid)
            else:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                    tf.write(body)
                    tmp_vid = tf.name
                video_path = Path(tmp_vid)
        elif body and "application/json" in ctype:
            payload = json.loads(body.decode("utf-8"))
            if payload.get("video_path"):
                video_path = Path(str(payload["video_path"]))

        result = await asyncio.to_thread(
            localize_video_on_world,
            video_path or DEFAULT_VIDEO_PATH,
            world.points,
            reconstructor.keyframes,
        )
        if not result.get("ok"):
            return result

        pos = result["target_position"]
        label = result.get("target_label", "Water Bottle")
        obj = SpatialObject(
            id="obj-water-bottle",
            label=label,
            position=pos,
            seen_by=f"Video t={result['target_t']}s (KF #{result['matched_keyframe'] + 1})",
            timestamp=time.time(),
            confidence=float(result.get("confidence", 0.5)),
        )
        pin = SpatialPin(
            id="pin-water-bottle",
            author="Meta AI",
            text="You left your water bottle on the ground beside the left bed.",
            pin_type="bottle",
            position=pos,
            landmark=label,
            created_at=time.time(),
            age_label=f"t={result['target_t']}s",
        )
        world.objects[obj.id] = obj
        world.pins[pin.id] = pin
        world.video_trail = list(result.get("video_trail", []))
        db.upsert_object(obj.model_dump())
        db.upsert_pin(pin.model_dump())
        await broadcast_state()
        return {
            **result,
            "object": obj.model_dump(),
            "pin": pin.model_dump(),
        }
    finally:
        if tmp_vid and os.path.exists(tmp_vid):
            os.remove(tmp_vid)


class CtrlFQuery(BaseModel):
    wearer_id: str = "wearer-b"
    query: str
    viewer_id: Optional[str] = None
    world_id: Optional[str] = None


def _match_objects_for_query(q_raw: str, candidates: List[SpatialObject]) -> List[SpatialObject]:
    q = q_raw.lower().strip()
    if any(p in q for p in ("left anything", "left behind", "anything behind", "forgot")):
        return sorted(candidates, key=lambda o: o.timestamp, reverse=True)
    matches = [o for o in candidates if q in o.label.lower() or q.rstrip("s") in o.label.lower()]
    if not matches:
        stop = {"where", "the", "find", "have", "seen", "left", "with", "for", "was", "are", "you"}
        tokens = [
            t.rstrip("s") if len(t) > 3 else t
            for t in q.replace("?", "").replace("'", "").split()
            if len(t) > 2 and t not in stop
        ]
        matches = [o for o in candidates if any(t in o.label.lower() for t in tokens)]
    return matches


@app.post("/api/ctrl-f")
async def ctrl_f_search(req: CtrlFQuery) -> Dict[str, Any]:
    vid = resolve_viewer_id(req.viewer_id or WEARER_TO_USER.get(req.wearer_id))
    visible = [o for o in world.objects.values() if can_user_view(o.model_dump(), vid, db)]
    matches = _match_objects_for_query(req.query, visible)
    if not matches:
        return {"found": False, "message": f"No teammate has seen '{req.query}' yet."}

    target = matches[0]
    wearer = world.wearers.get(req.wearer_id)
    events = db.list_object_events(world_id=target.world_id, object_id=target.id, limit=25)
    zone_label = target.zone_name or "Room"
    if not wearer:
        spoken = build_ask_answer(target.label, zone_label, 0.0, "nearby", events)
        return {
            "found": True,
            "object": target.model_dump(),
            "directions": spoken,
            "spoken_answer": spoken,
            "history": events,
        }

    nav = _compute_directions(wearer, target)
    spoken = build_ask_answer(target.label, zone_label, nav["distance_m"], nav["turn"], events)
    return {
        "found": True,
        "object": target.model_dump(),
        "path": [wearer.position, target.position],
        **nav,
        "spoken_answer": spoken,
        "history": events,
    }


# ============================================================================
# Everyday Consumer Multiplayer APIs (Social, Zones, Movement Ledger, Ask QA)
# ============================================================================

@app.get("/api/social/bootstrap")
async def social_bootstrap(viewer_id: Optional[str] = None) -> Dict[str, Any]:
    vid = resolve_viewer_id(viewer_id)
    users = db.list_users()
    viewer = next((u for u in users if u["user_id"] == vid), {"user_id": "guest", "name": "Guest", "handle": "@guest", "avatar_color": "#64748b", "friend_code": "GUEST"})
    friend_ids = set(db.list_friend_ids(vid)) if vid != "guest" else set()
    friends = [u for u in users if u["user_id"] in friend_ids]
    groups = db.list_groups(user_id=vid) if vid != "guest" else []
    spaces = [s for s in db.list_spaces_meta() if can_user_view(s, vid, db)]
    return {
        "viewer": viewer,
        "users": users,
        "friends": friends,
        "groups": groups,
        "spaces": spaces,
        "scan_jobs": list(world.scan_jobs.values()),
    }


class CreateProfileReq(BaseModel):
    name: str
    handle: Optional[str] = None


@app.post("/api/social/profiles")
async def create_profile(req: CreateProfileReq) -> Dict[str, Any]:
    clean_name = req.name.strip() or "Friend"
    slug = "".join(c for c in clean_name.lower() if c.isalnum())[:12] or uuid.uuid4().hex[:6]
    uid = f"usr-{slug}-{uuid.uuid4().hex[:4]}"
    handle = req.handle.strip() if req.handle else f"@{slug}"
    if not handle.startswith("@"):
        handle = f"@{handle}"
    user = {
        "user_id": uid,
        "name": clean_name,
        "handle": handle,
        "avatar_color": "#38bdf8",
        "friend_code": uuid.uuid4().hex[:6].upper(),
    }
    db.upsert_user(user)
    wid = f"wearer-{slug}"
    world.wearers[wid] = WearerState(
        wearer_id=wid,
        name=clean_name,
        user_id=uid,
        avatar_color="#38bdf8",
        position=[0.2, 1.4, 0.2],
        yaw=0.0,
        world_id=world.active_world_id,
        last_seen=time.time(),
    )
    await broadcast_state()
    return {"ok": True, "user": user}


class AddFriendReq(BaseModel):
    user_id: str
    target_handle_or_code: str


@app.post("/api/social/friends/add")
async def add_friend_endpoint(req: AddFriendReq) -> Dict[str, Any]:
    target = db.find_user_by_handle_or_code(req.target_handle_or_code)
    if target is None:
        return {"ok": False, "error": f"No user found matching '{req.target_handle_or_code}'"}
    db.add_friendship(req.user_id, target["user_id"])
    await broadcast_state()
    return {"ok": True, "friend": target}


class CreateGroupReq(BaseModel):
    owner_id: str
    name: str
    kind: str = "custom"
    member_ids: List[str] = []


@app.post("/api/social/groups")
async def create_group_endpoint(req: CreateGroupReq) -> Dict[str, Any]:
    gid = f"grp-{uuid.uuid4().hex[:8]}"
    members = list(dict.fromkeys([req.owner_id, *req.member_ids]))
    grp = {
        "group_id": gid,
        "name": req.name.strip() or "New Group",
        "kind": req.kind,
        "owner_id": req.owner_id,
        "invite_code": uuid.uuid4().hex[:6].upper(),
        "member_ids": members,
    }
    db.create_group(grp)
    await broadcast_state()
    return {"ok": True, "group": grp}


class JoinGroupReq(BaseModel):
    user_id: str
    invite_code: str


@app.post("/api/social/groups/join")
async def join_group_endpoint(req: JoinGroupReq) -> Dict[str, Any]:
    grp = db.find_group_by_invite(req.invite_code)
    if grp is None:
        return {"ok": False, "error": "Invalid group invite code"}
    db.join_group(grp["group_id"], req.user_id)
    await broadcast_state()
    return {"ok": True, "group": grp}


class ShareSpaceReq(BaseModel):
    world_id: str
    viewer_id: str
    display_name: Optional[str] = None
    audience_type: str = "group"
    group_ids: List[str] = []


class AutoIndexSpaceReq(BaseModel):
    viewer_id: str = "usr-conor"


def _apply_auto_index_result(
    world_id: str,
    zones: List[Dict[str, Any]],
    objs: List[Dict[str, Any]],
    actor_user_id: str = "usr-conor",
) -> Dict[str, Any]:
    """Persist auto-discovered Zones & Objects to DB and sync world.objects in memory."""
    summary = persist_indexed_world(
        world_id,
        db,
        zones,
        objs,
        actor_user_id=actor_user_id,
        clear_auto=True,
    )
    stale_prefix = f"auto-obj-{world_id}-"
    for oid in [k for k in list(world.objects.keys()) if k.startswith(stale_prefix)]:
        world.objects.pop(oid, None)
    for rec in summary.get("objects", []):
        world.objects[rec["id"]] = SpatialObject(**rec)
    return summary


@app.post("/api/spaces/{world_id}/auto-index")
async def auto_index_space_endpoint(world_id: str, req: AutoIndexSpaceReq = AutoIndexSpaceReq()) -> Dict[str, Any]:
    """Automatically detect Zones and Tracked Objects from keyframes + 3D splat point cloud."""
    payload = world.get_world_payload(world_id)
    pts = payload.get("points") if payload else world.points
    kfs = payload.get("keyframes") if payload else reconstructor.keyframes
    if not pts or not kfs:
        return {"ok": False, "error": f"No 3D points or keyframes available for {world_id}"}

    zones: List[Dict[str, Any]] = []
    objs: List[Dict[str, Any]] = []
    if world_id == "world_1" and DEFAULT_ROOM_PHOTOS_DIR.exists():
        zones, objs = await asyncio.to_thread(
            auto_index_from_photos,
            pts,
            kfs,
            DEFAULT_ROOM_PHOTOS_DIR,
            1,
            True,
        )
        if zones or objs:
            cache_path = _STATIC_DIR / "world_1_auto_index.json"
            cache_path.write_text(
                json.dumps({"world_id": "world_1", "zones": zones, "objects": objs}, indent=2),
                encoding="utf-8",
            )
    else:
        scans_dir = _STATIC_DIR / "dat_scans"
        vids = sorted(scans_dir.glob("*.mp4")) if scans_dir.exists() else []
        if vids:
            zones, objs = await asyncio.to_thread(
                auto_index_from_video,
                pts,
                kfs,
                vids[-1],
                1,
                True,
            )

    summary = _apply_auto_index_result(world_id, zones, objs, actor_user_id=req.viewer_id or "usr-conor")
    await broadcast_state()
    return {"ok": True, **summary}


@app.post("/api/spaces/share")
async def share_space_endpoint(req: ShareSpaceReq) -> Dict[str, Any]:
    existing = next((s for s in db.list_spaces_meta() if s["world_id"] == req.world_id), None)
    meta = {
        "world_id": req.world_id,
        "display_name": req.display_name or (existing["display_name"] if existing else req.world_id),
        "owner_id": existing["owner_id"] if existing else req.viewer_id,
        "audience_type": req.audience_type,
        "group_ids": req.group_ids,
    }
    db.upsert_space_meta(meta)
    await broadcast_state()
    return {"ok": True, "space": meta}


class CreateZoneReq(BaseModel):
    world_id: str = "world_1"
    name: str
    position: List[float]
    radius_m: float = 1.2
    created_by: str = "usr-conor"


@app.get("/api/zones")
async def get_zones_endpoint(world_id: Optional[str] = None) -> Dict[str, Any]:
    return {"zones": world.db.list_zones(world_id=world_id)}


@app.post("/api/zones")
async def create_zone_endpoint(req: CreateZoneReq) -> Dict[str, Any]:
    zone = {
        "zone_id": f"zone-{uuid.uuid4().hex[:8]}",
        "world_id": req.world_id,
        "name": req.name.strip() or "Named Zone",
        "x": float(req.position[0]),
        "y": float(req.position[1]),
        "z": float(req.position[2]),
        "radius_m": float(req.radius_m),
        "created_by": req.created_by,
    }
    world.db.upsert_zone(zone)
    await broadcast_state()
    return {"ok": True, "zone": zone}


class ObjectSightingReq(BaseModel):
    world_id: str = "world_1"
    label: str
    position: List[float]
    actor_user_id: str = "usr-conor"
    zone_hint: Optional[str] = None
    confidence: float = 0.9
    audience_type: str = "group"
    group_ids: List[str] = ["grp-room"]
    unlocalized_2d: bool = False


@app.post("/api/objects/sighting")
async def record_object_sighting(req: ObjectSightingReq) -> Dict[str, Any]:
    if req.unlocalized_2d:
        return {"ok": True, "skipped": "unlocalized_2d"}
    now = time.time()
    slug = "".join(c if c.isalnum() else "-" for c in req.label.lower().strip()).strip("-")[:24] or "item"
    obj_id = f"obj-{slug}"
    actor = world.db.get_user(req.actor_user_id)
    actor_name = actor["name"] if actor else req.actor_user_id

    zones = world.db.list_zones(world_id=req.world_id)
    resolved_zone = resolve_zone(req.world_id, req.position, zones, req.zone_hint)
    prev = world.objects.get(obj_id)
    prev_dict = prev.model_dump() if prev else None
    ev_type, event = evaluate_sighting_transition(
        prev_obj=prev_dict,
        prev_zone=prev.zone_name if prev else None,
        new_pos=req.position,
        new_zone=resolved_zone,
        actor_user_id=req.actor_user_id,
        actor_name=actor_name,
        label=req.label.strip(),
        object_id=obj_id,
        world_id=req.world_id,
        ts=now,
    )

    obj = SpatialObject(
        id=obj_id,
        label=req.label.strip(),
        position=[round(float(v), 3) for v in req.position[:3]],
        seen_by=actor_name,
        timestamp=now,
        confidence=req.confidence,
        world_id=req.world_id,
        zone_name=resolved_zone,
        owner_id=prev.owner_id if prev else req.actor_user_id,
        audience_type=prev.audience_type if prev else req.audience_type,
        group_ids=prev.group_ids if prev else req.group_ids,
    )
    world.objects[obj.id] = obj
    world.db.upsert_object(obj.model_dump())
    if event is not None:
        world.db.append_object_event(event)
    await broadcast_state()
    return {"ok": True, "object": obj.model_dump(), "event_type": ev_type, "event": event}


@app.get("/api/objects/history")
async def get_object_history(
    world_id: Optional[str] = None,
    query: Optional[str] = None,
    viewer_id: Optional[str] = None,
) -> Dict[str, Any]:
    vid = resolve_viewer_id(viewer_id)
    visible_obj_ids = {
        o.id for o in world.objects.values() if can_user_view(o.model_dump(), vid, db)
    }
    events = db.list_object_events(world_id=world_id, limit=120)
    filtered = [e for e in events if e["object_id"] in visible_obj_ids]
    if query:
        q = query.strip().lower()
        filtered = [e for e in filtered if q in e["label"].lower() or q in e["summary"].lower()]
    return {"events": filtered}


class AskSpaceReq(BaseModel):
    viewer_id: Optional[str] = None
    wearer_id: str = "wearer-b"
    query: str
    world_id: Optional[str] = None


@app.post("/api/ask")
async def ask_space_qa(req: AskSpaceReq) -> Dict[str, Any]:
    vid = resolve_viewer_id(req.viewer_id or WEARER_TO_USER.get(req.wearer_id))
    visible_objs = [o for o in world.objects.values() if can_user_view(o.model_dump(), vid, db)]
    if req.world_id:
        scoped = [o for o in visible_objs if o.world_id == req.world_id]
        if scoped:
            visible_objs = scoped

    gemini_res = await answer_space_question_with_gemini(
        req.query, [o.model_dump() for o in visible_objs]
    )
    matches = _match_objects_for_query(req.query, visible_objs)
    target = None
    if gemini_res and gemini_res.get("object_id"):
        target = next((o for o in visible_objs if o.id == gemini_res["object_id"]), None)
    if target is None and matches:
        if req.world_id:
            matches.sort(key=lambda o: (0 if o.world_id == req.world_id else 1, -o.timestamp))
        target = matches[0]

    if target is None:
        return {
            "found": False,
            "provider": "gemini-3.8-flash" if gemini_res else "local-matcher",
            "spoken_answer": (
                gemini_res["spoken_answer"]
                if gemini_res
                else f"I haven't seen '{req.query}' in your shared spaces yet."
            ),
        }

    wearer_id = USER_TO_WEARER.get(vid, req.wearer_id)
    wearer = world.wearers.get(wearer_id) or world.wearers.get(req.wearer_id)
    nav = _compute_directions(wearer, target) if wearer else {"distance_m": 0.0, "bearing_deg": 0, "arrow": "↑", "turn": "nearby", "directions": ""}
    events = db.list_object_events(world_id=target.world_id, object_id=target.id, limit=25)
    zone_label = target.zone_name or "Room"
    spoken = (
        gemini_res["spoken_answer"]
        if gemini_res
        else build_ask_answer(target.label, zone_label, nav["distance_m"], nav["turn"], events)
    )
    spaces_by_id = {s["world_id"]: s["display_name"] for s in db.list_spaces_meta()}
    return {
        "found": True,
        "provider": "gemini-3.8-flash" if gemini_res else "local-matcher",
        "world_id": target.world_id,
        "space_name": spaces_by_id.get(target.world_id, target.world_id),
        "object": target.model_dump(),
        "history": events,
        "latest_move": next((e for e in reversed(events) if e.get("event_type") == "moved"), None),
        "path": [wearer.position, target.position] if wearer else [target.position],
        **nav,
        "spoken_answer": spoken,
    }


@app.post("/api/snapshot")
async def create_snapshot(label: str = "checkpoint") -> Dict[str, Any]:
    snap = world.take_snapshot(label)
    await broadcast_state()
    return snap.model_dump()


@app.post("/api/transcribe")
async def transcribe_pcm_endpoint(request: Request) -> Dict[str, Any]:
    body = await request.body()
    if len(body) < 3200:
        return {"text": "", "model": "nvidia/parakeet-unified-en-0.6b", "device": "cuda:0"}
    # Try local Parakeet server on :8765 first; fall back to Gemini 3.8 Flash audio ASR
    if request.headers.get("X-Relay-From-8765") != "1":
        try:
            async with httpx.AsyncClient(timeout=3.5) as client:
                r = await client.post(
                    "http://127.0.0.1:8765/api/transcribe",
                    content=body,
                    headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": "16000"},
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("text"):
                        return data
        except Exception:
            pass
    text = await transcribe_pcm_with_gemini(body, 16000)
    return {"text": text, "model": "nvidia/parakeet-unified-en-0.6b", "device": "cuda:0"}


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
    if not pin.id:
        pin.id = f"pin-{uuid.uuid4().hex[:8]}"
    if not pin.created_at:
        pin.created_at = time.time()
    if not pin.author_id:
        matched_user = next((u for u in db.list_users() if u["name"].lower() == pin.author.lower()), None)
        if matched_user:
            pin.author_id = matched_user["user_id"]
    world.pins[pin.id] = pin
    pin_dict = pin.model_dump()
    db.upsert_pin(pin_dict)
    await broadcast_state()
    return {"ok": True, "pin": pin_dict, **pin_dict}


class HudPinRequest(BaseModel):
    id: Optional[str] = None
    label: str = "object"
    text: str
    author: str = "Person A"
    center_px: Optional[List[float]] = None
    frame_size_px: Optional[List[float]] = None
    position_hint: Optional[List[float]] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None


def _raycast_world_point(
    origin: np.ndarray,
    direction: np.ndarray,
    points: List[List[float]],
) -> List[float]:
    """Find the nearest 3D surface point in world.points along a camera ray."""
    if not points:
        pt = origin + direction * 1.5
        return [round(float(v), 3) for v in pt]
    arr = np.asarray(points, dtype=np.float32)[:, :3]
    rel = arr - origin[None, :]
    proj = rel @ direction
    mask = (proj > 0.35) & (proj < 6.0)
    if not np.any(mask):
        pt = origin + direction * 1.5
        return [round(float(v), 3) for v in pt]
    rel_m = rel[mask]
    proj_m = proj[mask]
    perp = np.linalg.norm(rel_m - proj_m[:, None] * direction[None, :], axis=1)
    # Within ~6 degrees of the ray
    cone = perp < (proj_m * 0.11)
    if np.any(cone):
        hits = arr[mask][cone]
        dists = proj_m[cone]
        near_d = float(np.percentile(dists, 15))
        cluster = hits[dists <= near_d + 0.25]
        pt = np.median(cluster, axis=0)
        return [round(float(v), 3) for v in pt]
    pt = origin + direction * 1.5
    return [round(float(v), 3) for v in pt]


@app.post("/api/pin-from-hud")
async def add_pin_from_hud(req: HudPinRequest) -> Dict[str, Any]:
    if req.position_hint and len(req.position_hint) == 3:
        pos = [round(float(v), 3) for v in req.position_hint]
    else:
        wearer = world.wearers.get("wearer-b")
        origin = np.array(wearer.position if wearer else [0.0, 1.3, 0.0], dtype=np.float32)
        yaw_deg = float(req.yaw if req.yaw is not None else (wearer.yaw if wearer else 0.0))
        pitch_deg = float(req.pitch if req.pitch is not None else (wearer.pitch if wearer else 0.0))
        if req.center_px and req.frame_size_px and req.frame_size_px[0] > 0 and req.frame_size_px[1] > 0:
            nx = (req.center_px[0] / req.frame_size_px[0]) - 0.5
            ny = (req.center_px[1] / req.frame_size_px[1]) - 0.5
            yaw_deg += nx * 65.0
            pitch_deg -= ny * 50.0
        yaw_r = math.radians(yaw_deg)
        pitch_r = math.radians(pitch_deg)
        direction = np.array([
            math.sin(yaw_r) * math.cos(pitch_r),
            math.sin(pitch_r),
            -math.cos(yaw_r) * math.cos(pitch_r),
        ], dtype=np.float32)
        direction /= max(1e-6, float(np.linalg.norm(direction)))
        pos = _raycast_world_point(origin, direction, world.points)

    pin_id = req.id or f"pin-{uuid.uuid4().hex[:8]}"
    pin = SpatialPin(
        id=pin_id,
        author=req.author,
        text=req.text,
        pin_type="note",
        position=pos,
        landmark=req.label,
        created_at=time.time(),
        age_label="just now",
    )
    world.pins[pin.id] = pin
    db.upsert_pin(pin.model_dump())
    await broadcast_state()
    return {"ok": True, "position": pos, "pin": pin.model_dump()}


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
    qp = websocket.query_params
    viewer_id = resolve_viewer_id(qp.get("viewer_id"))
    world_id = qp.get("world_id") or world.active_world_id
    world.clients[websocket] = {"viewer_id": viewer_id, "world_id": world_id}
    await websocket.send_text(json.dumps(world.serialize_state(viewer_id=viewer_id, world_id=world_id)))
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "set_context":
                world.clients[websocket] = {
                    "viewer_id": resolve_viewer_id(msg.get("viewer_id")),
                    "world_id": msg.get("world_id") or world.active_world_id,
                }
                ctx = world.clients[websocket]
                await websocket.send_text(
                    json.dumps(world.serialize_state(viewer_id=ctx["viewer_id"], world_id=ctx["world_id"]))
                )

            elif msg_type == "pose":
                uid = msg.get("user_id") or WEARER_TO_USER.get(msg["wearer_id"])
                u = db.get_user(uid) if uid else None
                w = WearerState(
                    wearer_id=msg["wearer_id"],
                    name=msg.get("name") or (u["name"] if u else msg["wearer_id"]),
                    user_id=uid,
                    avatar_color=u["avatar_color"] if u else "#38bdf8",
                    world_id=msg.get("world_id") or world.active_world_id,
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
        world.clients.pop(websocket, None)
