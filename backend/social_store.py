"""Everyday multiplayer domain helpers: visibility, zones, object movement ledger, and default seeds."""

import math
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

LEGACY_INDUSTRIAL_OBJECT_IDS = {
    "obj-drill",
    "obj-socket",
    "obj-keys",
    "obj-medkit",
    "obj-battery",
    "obj-laser",
}
LEGACY_INDUSTRIAL_PIN_IDS = {"pin-1", "pin-2"}

WEARER_TO_USER: Dict[str, str] = {
    "wearer-b": "usr-conor",
    "wearer-a": "usr-alec",
}
USER_TO_WEARER: Dict[str, str] = {v: k for k, v in WEARER_TO_USER.items()}


def resolve_viewer_id(viewer_id: Optional[str]) -> str:
    """Default omitted viewer_id to Conor for legacy glasses compatibility; preserve 'guest'."""
    if viewer_id is None:
        return "usr-conor"
    cleaned = str(viewer_id).strip()
    if not cleaned:
        return "usr-conor"
    if cleaned.lower() == "guest":
        return "guest"
    return cleaned


def can_user_view(record: Dict[str, Any], viewer_id: Optional[str], db: Any) -> bool:
    """Enforce audience visibility (public, friends, group, people, private) for a viewer."""
    vid = resolve_viewer_id(viewer_id)
    author_id = record.get("author_id") or record.get("owner_id") or ""
    audience = (record.get("audience_type") or "public").lower()

    if vid != "guest" and author_id and author_id == vid:
        return True
    if audience == "public":
        return True
    if vid == "guest":
        return False
    if audience == "private":
        return False
    if audience == "friends":
        if not author_id:
            return False
        return vid in set(db.list_friend_ids(author_id))
    if audience == "group":
        rec_groups = set(record.get("group_ids") or [])
        if not rec_groups:
            return False
        viewer_groups = {g["group_id"] for g in db.list_groups(user_id=vid)}
        return bool(rec_groups & viewer_groups)
    if audience == "people":
        return vid in set(record.get("target_user_ids") or [])
    return False


def resolve_zone(
    world_id: str,
    position: List[float],
    zones: List[Dict[str, Any]],
    placement_hint: Optional[str] = None,
) -> str:
    """Resolve a 3D coordinate to the nearest matching Named Zone, else fallback hint."""
    best_name: Optional[str] = None
    best_dist = float("inf")
    px, py, pz = float(position[0]), float(position[1]), float(position[2])
    for z in zones:
        if z.get("world_id") and world_id and z["world_id"] != world_id:
            continue
        cx = float(z.get("x", 0.0))
        cy = float(z.get("y", 0.0))
        cz = float(z.get("z", 0.0))
        radius = float(z.get("radius_m", 1.2))
        dist = math.sqrt((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2)
        if dist <= radius and dist < best_dist:
            best_dist = dist
            best_name = z["name"]
    if best_name:
        return best_name
    hint = (placement_hint or "").strip()
    return hint if hint else "Room"


def evaluate_sighting_transition(
    prev_obj: Optional[Dict[str, Any]],
    prev_zone: Optional[str],
    new_pos: List[float],
    new_zone: str,
    actor_user_id: str,
    actor_name: str,
    label: str,
    object_id: str,
    world_id: str,
    ts: Optional[float] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Distinguish initial placement, actual physical move (>=0.35m or zone change), and passive re-sighting."""
    now = ts if ts is not None else time.time()
    if prev_obj is None:
        return (
            "placed",
            {
                "event_id": f"evt-{uuid.uuid4().hex[:10]}",
                "object_id": object_id,
                "label": label,
                "world_id": world_id,
                "event_type": "placed",
                "actor_user_id": actor_user_id,
                "actor_name": actor_name,
                "from_zone": "",
                "from_pos": None,
                "to_zone": new_zone,
                "to_pos": [round(float(v), 3) for v in new_pos[:3]],
                "summary": f"{actor_name} left {label} at {new_zone}",
                "timestamp": now,
            },
        )

    old_pos = prev_obj.get("position") or [0.0, 0.0, 0.0]
    disp = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(old_pos[:3], new_pos[:3])))
    old_z = (prev_zone or prev_obj.get("zone_name") or "").strip()
    zone_changed = bool(new_zone and old_z and new_zone.lower() != old_z.lower())

    if not zone_changed and disp < 0.35:
        return ("seen", None)

    from_label = old_z or "previous spot"
    return (
        "moved",
        {
            "event_id": f"evt-{uuid.uuid4().hex[:10]}",
            "object_id": object_id,
            "label": label,
            "world_id": world_id,
            "event_type": "moved",
            "actor_user_id": actor_user_id,
            "actor_name": actor_name,
            "from_zone": from_label,
            "from_pos": [round(float(v), 3) for v in old_pos[:3]],
            "to_zone": new_zone,
            "to_pos": [round(float(v), 3) for v in new_pos[:3]],
            "summary": f"{actor_name} moved {label} from {from_label} to {new_zone}",
            "timestamp": now,
        },
    )


def build_ask_answer(
    item_label: str,
    current_zone: str,
    distance_m: float,
    turn: str,
    events: List[Dict[str, Any]],
) -> str:
    """Format a natural spoken answer explaining who placed/moved the item and where it is now."""
    moved_events = [e for e in events if e.get("event_type") == "moved"]
    if moved_events:
        latest = moved_events[-1]
        mover = latest.get("actor_name") or "Someone"
        from_z = latest.get("from_zone") or "its previous spot"
        to_z = latest.get("to_zone") or current_zone
        placed = next((e for e in events if e.get("event_type") == "placed"), None)
        if placed and placed.get("actor_name") and placed["actor_name"] != mover:
            return (
                f"{placed['actor_name']} left your {item_label} at {from_z}, "
                f"and {mover} moved it to {to_z} ({distance_m}m {turn})."
            )
        return (
            f"{mover} moved your {item_label} from {from_z} to {to_z} "
            f"({distance_m}m {turn})."
        )
    if events:
        latest = events[-1]
        actor = latest.get("actor_name") or "You"
        zone = latest.get("to_zone") or current_zone
        return f"{actor} left your {item_label} at {zone} ({distance_m}m {turn})."
    return f"Your {item_label} is at {current_zone} ({distance_m}m {turn})."


def seed_everyday_social_data(db: Any) -> None:
    """Idempotently seed Conor and Alec profiles, shared hotel room group, and zones."""
    now = time.time()
    default_users = [
        {"user_id": "usr-conor", "name": "Conor", "handle": "@conor", "avatar_color": "#2997ff", "friend_code": "CONOR1"},
        {"user_id": "usr-alec", "name": "Alec", "handle": "@alec", "avatar_color": "#34d399", "friend_code": "ALEC22"},
    ]
    existing_users = {u["user_id"] for u in db.list_users()}
    for u in default_users:
        if u["user_id"] not in existing_users:
            db.upsert_user(u)

    # Friendship edge between Conor and Alec
    if "usr-alec" not in set(db.list_friend_ids("usr-conor")):
        db.add_friendship("usr-conor", "usr-alec")

    existing_groups = {g["group_id"] for g in db.list_groups()}
    if "grp-room" not in existing_groups:
        db.create_group({
            "group_id": "grp-room",
            "name": "Hotel Room",
            "kind": "roommates",
            "owner_id": "usr-conor",
            "invite_code": "ROOM-01",
            "member_ids": ["usr-conor", "usr-alec"],
        })

    existing_spaces = {s["world_id"] for s in db.list_spaces_meta()}
    if "world_1" not in existing_spaces:
        db.upsert_space_meta({
            "world_id": "world_1",
            "display_name": "Hotel Room",
            "owner_id": "usr-conor",
            "audience_type": "group",
            "group_ids": ["grp-room"],
        })

    if not db.list_zones("world_1"):
        for zid, name, x, y, z in (
            ("zone-beside-left-bed", "Beside Left Bed", -0.72, 0.06, 0.70),
            ("zone-right-nightstand", "Right Nightstand", 0.72, 0.25, 0.70),
            ("zone-foot-of-bed", "Foot of Bed", 0.0, 0.15, -0.45),
            ("zone-room-entrance", "Room Entrance", 1.68, 0.20, -0.10),
        ):
            db.upsert_zone({
                "zone_id": zid,
                "world_id": "world_1",
                "name": name,
                "x": x,
                "y": y,
                "z": z,
                "radius_m": 0.95,
                "created_by": "usr-conor",
            })

    if not db.list_objects():
        db.upsert_object({
            "id": "obj-water-bottle",
            "label": "Water Bottle",
            "category": "everyday",
            "position": [-0.72, 0.06, 0.70],
            "owner": "Conor",
            "owner_id": "usr-conor",
            "last_seen_by_id": "usr-conor",
            "last_seen_by_name": "Conor",
            "last_seen_at": now - 120,
            "zone_name": "Beside Left Bed",
            "world_id": "world_1",
            "audience_type": "group",
            "group_ids": ["grp-room"],
            "timestamp": now - 120,
            "confidence": 0.96,
            "seen_by": "Conor",
        })
        db.append_object_event({
            "event_id": "evt-water-bottle-init",
            "object_id": "obj-water-bottle",
            "label": "Water Bottle",
            "world_id": "world_1",
            "event_type": "placed",
            "actor_user_id": "usr-conor",
            "actor_name": "Conor",
            "from_zone": "",
            "from_pos": None,
            "to_zone": "Beside Left Bed",
            "to_pos": [-0.72, 0.06, 0.70],
            "summary": "Conor left Water Bottle at Beside Left Bed",
            "timestamp": now - 120,
        })

    # Seed clean everyday friend notes if none exist
    if not db.list_pins():
        db.upsert_pin({
            "id": "pin-subway-review",
            "author": "Alec",
            "author_id": "usr-alec",
            "text": "Great Italian BMT and fresh cookies — fast lunch stop.",
            "pin_type": "friend_memory",
            "position": [0.0, 1.2, 0.0],
            "landmark": "Subway — Otaniemi",
            "place_name": "Subway — Otaniemi",
            "rating": 5,
            "created_at": now - 7200,
            "age_label": "2h ago",
            "world_id": None,
            "audience_type": "friends",
            "group_ids": [],
            "target_user_ids": [],
            "lat": 60.1841,
            "lon": 24.8301,
        })
        db.upsert_pin({
            "id": "pin-espresso-public",
            "author": "Conor",
            "author_id": "usr-conor",
            "text": "Best flat white & cardamom bun near campus.",
            "pin_type": "friend_memory",
            "position": [0.0, 1.2, 0.0],
            "landmark": "Corner Espresso Bar",
            "place_name": "Corner Espresso Bar",
            "rating": 5,
            "created_at": now - 86400,
            "age_label": "1d ago",
            "world_id": None,
            "audience_type": "friends",
            "group_ids": [],
            "target_user_ids": [],
            "lat": 60.1852,
            "lon": 24.8284,
        })
