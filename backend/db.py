"""
Persistence layer for WorldGit backed by Aiven PostgreSQL,
with automatic local SQLite fallback when AIVEN_DATABASE_URL is not configured.
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from social_store import LEGACY_INDUSTRIAL_OBJECT_IDS, LEGACY_INDUSTRIAL_PIN_IDS


class SpatialDatabase:
    def __init__(self, sqlite_path: Optional[Path] = None) -> None:
        self.aiven_url: Optional[str] = os.getenv("AIVEN_DATABASE_URL") if sqlite_path is None else None
        self.sqlite_path = sqlite_path or (Path(__file__).resolve().parent / "worldgit_aiven_cache.db")
        self.backend = "sqlite-fallback"
        self.connected = False
        self.error: Optional[str] = None
        self._pg_conn = None
        self._sqlite_conn = None
        self._init_connection()

    def _init_connection(self) -> None:
        if self.aiven_url:
            try:
                import psycopg

                self._pg_conn = psycopg.connect(self.aiven_url, autocommit=True)
                self.backend = "aiven-postgresql"
                self.connected = True
                self._init_schema(is_pg=True)
                return
            except Exception as exc:
                self.error = str(exc)

        self._sqlite_conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self._sqlite_conn.row_factory = sqlite3.Row
        self.backend = "sqlite-local-cache"
        self.connected = True
        self._init_schema(is_pg=False)

    def _exec(self, sql: str, params: tuple = ()) -> None:
        if self._pg_conn is not None:
            pg_sql = sql.replace("?", "%s")
            with self._pg_conn.cursor() as cur:
                cur.execute(pg_sql, params)
        elif self._sqlite_conn is not None:
            cur = self._sqlite_conn.cursor()
            cur.execute(sql, params)
            self._sqlite_conn.commit()

    def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        if self._pg_conn is not None:
            pg_sql = sql.replace("?", "%s")
            with self._pg_conn.cursor() as cur:
                cur.execute(pg_sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        elif self._sqlite_conn is not None:
            cur = self._sqlite_conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        return []

    def _try_alter(self, stmt: str) -> None:
        try:
            self._exec(stmt)
        except Exception:
            pass

    def _init_schema(self, is_pg: bool) -> None:
        double_t = "DOUBLE PRECISION" if is_pg else "REAL"
        int_t = "INTEGER"
        self._exec(
            f"""
            CREATE TABLE IF NOT EXISTS spatial_objects (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                z REAL NOT NULL,
                seen_by TEXT NOT NULL,
                ts {double_t} NOT NULL,
                world_id TEXT DEFAULT 'world_1',
                zone_name TEXT DEFAULT '',
                owner_id TEXT DEFAULT 'usr-conor',
                audience_type TEXT DEFAULT 'group',
                group_ids_json TEXT DEFAULT '["grp-room"]'
            )
            """
        )
        self._exec(
            f"""
            CREATE TABLE IF NOT EXISTS spatial_pins (
                id TEXT PRIMARY KEY,
                author TEXT NOT NULL,
                text TEXT NOT NULL,
                pin_type TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                z REAL NOT NULL,
                landmark TEXT NOT NULL,
                created_at {double_t} NOT NULL,
                age_label TEXT NOT NULL,
                world_id TEXT,
                author_id TEXT,
                audience_type TEXT DEFAULT 'public',
                group_ids_json TEXT DEFAULT '[]',
                target_user_ids_json TEXT DEFAULT '[]',
                rating {int_t},
                place_name TEXT,
                lat {double_t},
                lon {double_t}
            )
            """
        )
        self._exec(
            f"""
            CREATE TABLE IF NOT EXISTS world_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                ts {double_t} NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                handle TEXT NOT NULL,
                avatar_color TEXT NOT NULL,
                friend_code TEXT NOT NULL
            )
            """
        )
        self._exec(
            f"""
            CREATE TABLE IF NOT EXISTS friendships (
                user_a TEXT NOT NULL,
                user_b TEXT NOT NULL,
                created_at {double_t} NOT NULL,
                PRIMARY KEY (user_a, user_b)
            )
            """
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                invite_code TEXT NOT NULL
            )
            """
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            )
            """
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS spaces_meta (
                world_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                audience_type TEXT NOT NULL,
                group_ids_json TEXT NOT NULL
            )
            """
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS spatial_zones (
                zone_id TEXT PRIMARY KEY,
                world_id TEXT NOT NULL,
                name TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                z REAL NOT NULL,
                radius_m REAL NOT NULL,
                created_by TEXT NOT NULL
            )
            """
        )
        self._exec(
            f"""
            CREATE TABLE IF NOT EXISTS object_events (
                event_id TEXT PRIMARY KEY,
                object_id TEXT NOT NULL,
                label TEXT NOT NULL,
                world_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                from_zone TEXT NOT NULL,
                from_pos_json TEXT,
                to_zone TEXT NOT NULL,
                to_pos_json TEXT,
                summary TEXT NOT NULL,
                ts {double_t} NOT NULL
            )
            """
        )

        # Additive column migrations for pre-existing tables
        for col_sql in (
            "ALTER TABLE spatial_objects ADD COLUMN world_id TEXT DEFAULT 'world_1'",
            "ALTER TABLE spatial_objects ADD COLUMN zone_name TEXT DEFAULT ''",
            "ALTER TABLE spatial_objects ADD COLUMN owner_id TEXT DEFAULT 'usr-conor'",
            "ALTER TABLE spatial_objects ADD COLUMN audience_type TEXT DEFAULT 'group'",
            "ALTER TABLE spatial_objects ADD COLUMN group_ids_json TEXT DEFAULT '[\"grp-room\"]'",
            "ALTER TABLE spatial_pins ADD COLUMN world_id TEXT",
            "ALTER TABLE spatial_pins ADD COLUMN author_id TEXT",
            "ALTER TABLE spatial_pins ADD COLUMN audience_type TEXT DEFAULT 'public'",
            "ALTER TABLE spatial_pins ADD COLUMN group_ids_json TEXT DEFAULT '[]'",
            "ALTER TABLE spatial_pins ADD COLUMN target_user_ids_json TEXT DEFAULT '[]'",
            "ALTER TABLE spatial_pins ADD COLUMN rating INTEGER",
            "ALTER TABLE spatial_pins ADD COLUMN place_name TEXT",
            f"ALTER TABLE spatial_pins ADD COLUMN lat {double_t}",
            f"ALTER TABLE spatial_pins ADD COLUMN lon {double_t}",
        ):
            self._try_alter(col_sql)

    def migrate_purge_legacy_industrial_seed(self) -> None:
        """Remove legacy industrial placeholder rows while preserving all user-created rows."""
        if LEGACY_INDUSTRIAL_OBJECT_IDS:
            marks = ",".join("?" for _ in LEGACY_INDUSTRIAL_OBJECT_IDS)
            self._exec(f"DELETE FROM spatial_objects WHERE id IN ({marks})", tuple(LEGACY_INDUSTRIAL_OBJECT_IDS))
        if LEGACY_INDUSTRIAL_PIN_IDS:
            marks = ",".join("?" for _ in LEGACY_INDUSTRIAL_PIN_IDS)
            self._exec(f"DELETE FROM spatial_pins WHERE id IN ({marks})", tuple(LEGACY_INDUSTRIAL_PIN_IDS))
        # Also remove industrial pins 3/4 if they match old hardcoded industrial seed landmarks
        self._exec("DELETE FROM spatial_pins WHERE id IN ('pin-3', 'pin-4') AND author IN ('Sam', 'Alex')")

    def upsert_object(self, obj: Dict[str, Any]) -> None:
        pos = obj["position"]
        self._exec(
            """
            INSERT INTO spatial_objects (
                id, label, x, y, z, seen_by, ts, world_id, zone_name, owner_id, audience_type, group_ids_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                label = excluded.label,
                x = excluded.x,
                y = excluded.y,
                z = excluded.z,
                seen_by = excluded.seen_by,
                ts = excluded.ts,
                world_id = excluded.world_id,
                zone_name = excluded.zone_name,
                owner_id = excluded.owner_id,
                audience_type = excluded.audience_type,
                group_ids_json = excluded.group_ids_json
            """,
            (
                obj["id"],
                obj["label"],
                float(pos[0]),
                float(pos[1]),
                float(pos[2]),
                obj["seen_by"],
                float(obj["timestamp"]),
                obj.get("world_id") or "world_1",
                obj.get("zone_name") or "",
                obj.get("owner_id") or "usr-conor",
                obj.get("audience_type") or "group",
                json.dumps(obj.get("group_ids") if obj.get("group_ids") is not None else ["grp-room"]),
            ),
        )

    def list_objects(self, world_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if world_id:
            rows = self._fetchall("SELECT * FROM spatial_objects WHERE world_id = ? ORDER BY ts DESC", (world_id,))
        else:
            rows = self._fetchall("SELECT * FROM spatial_objects ORDER BY ts DESC")
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "label": r["label"],
                "position": [float(r["x"]), float(r["y"]), float(r["z"])],
                "seen_by": r["seen_by"],
                "timestamp": float(r["ts"]),
                "world_id": r.get("world_id") or "world_1",
                "zone_name": r.get("zone_name") or "",
                "owner_id": r.get("owner_id") or "usr-conor",
                "audience_type": r.get("audience_type") or "group",
                "group_ids": json.loads(r.get("group_ids_json") or '["grp-room"]'),
            })
        return out

    def upsert_pin(self, pin: Dict[str, Any]) -> None:
        pos = pin["position"]
        self._exec(
            """
            INSERT INTO spatial_pins (
                id, author, text, pin_type, x, y, z, landmark, created_at, age_label,
                world_id, author_id, audience_type, group_ids_json, target_user_ids_json,
                rating, place_name, lat, lon
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                author = excluded.author,
                text = excluded.text,
                pin_type = excluded.pin_type,
                x = excluded.x,
                y = excluded.y,
                z = excluded.z,
                landmark = excluded.landmark,
                created_at = excluded.created_at,
                age_label = excluded.age_label,
                world_id = excluded.world_id,
                author_id = excluded.author_id,
                audience_type = excluded.audience_type,
                group_ids_json = excluded.group_ids_json,
                target_user_ids_json = excluded.target_user_ids_json,
                rating = excluded.rating,
                place_name = excluded.place_name,
                lat = excluded.lat,
                lon = excluded.lon
            """,
            (
                pin["id"],
                pin["author"],
                pin["text"],
                pin["pin_type"],
                float(pos[0]),
                float(pos[1]),
                float(pos[2]),
                pin["landmark"],
                float(pin["created_at"]),
                pin.get("age_label", "just now"),
                pin.get("world_id"),
                pin.get("author_id"),
                pin.get("audience_type", "public"),
                json.dumps(pin.get("group_ids") or []),
                json.dumps(pin.get("target_user_ids") or []),
                pin.get("rating"),
                pin.get("place_name"),
                pin.get("lat"),
                pin.get("lon"),
            ),
        )

    def list_pins(self, world_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if world_id:
            rows = self._fetchall(
                "SELECT * FROM spatial_pins WHERE world_id = ? OR world_id IS NULL ORDER BY created_at DESC",
                (world_id,),
            )
        else:
            rows = self._fetchall("SELECT * FROM spatial_pins ORDER BY created_at DESC")
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "author": r["author"],
                "text": r["text"],
                "pin_type": r["pin_type"],
                "position": [float(r["x"]), float(r["y"]), float(r["z"])],
                "landmark": r["landmark"],
                "created_at": float(r["created_at"]),
                "age_label": r["age_label"],
                "world_id": r.get("world_id"),
                "author_id": r.get("author_id"),
                "audience_type": r.get("audience_type") or "public",
                "group_ids": json.loads(r.get("group_ids_json") or "[]"),
                "target_user_ids": json.loads(r.get("target_user_ids_json") or "[]"),
                "rating": r.get("rating"),
                "place_name": r.get("place_name"),
                "lat": r.get("lat"),
                "lon": r.get("lon"),
            })
        return out

    def save_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO world_snapshots (snapshot_id, label, ts, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (snapshot_id) DO UPDATE SET
                label = excluded.label,
                ts = excluded.ts,
                payload_json = excluded.payload_json
            """,
            (
                snapshot["snapshot_id"],
                snapshot["label"],
                float(snapshot["timestamp"]),
                json.dumps(snapshot["objects"]),
            ),
        )

    def upsert_user(self, user: Dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO users (user_id, name, handle, avatar_color, friend_code)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                name = excluded.name,
                handle = excluded.handle,
                avatar_color = excluded.avatar_color,
                friend_code = excluded.friend_code
            """,
            (user["user_id"], user["name"], user["handle"], user["avatar_color"], user["friend_code"]),
        )

    def list_users(self) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM users ORDER BY name ASC")

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return rows[0] if rows else None

    def find_user_by_handle_or_code(self, token: str) -> Optional[Dict[str, Any]]:
        cleaned = token.strip().lower()
        for u in self.list_users():
            if u["handle"].lower() == cleaned or u["handle"].lstrip("@").lower() == cleaned.lstrip("@"):
                return u
            if u["friend_code"].lower() == cleaned or u["user_id"].lower() == cleaned:
                return u
        return None

    def add_friendship(self, user_a: str, user_b: str) -> None:
        if user_a == user_b:
            return
        a, b = sorted((user_a, user_b))
        self._exec(
            """
            INSERT INTO friendships (user_a, user_b, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT (user_a, user_b) DO NOTHING
            """,
            (a, b, time.time()),
        )

    def list_friend_ids(self, user_id: str) -> List[str]:
        rows = self._fetchall("SELECT user_a, user_b FROM friendships WHERE user_a = ? OR user_b = ?", (user_id, user_id))
        return [r["user_b"] if r["user_a"] == user_id else r["user_a"] for r in rows]

    def create_group(self, group: Dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO groups (group_id, name, kind, owner_id, invite_code)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (group_id) DO UPDATE SET
                name = excluded.name,
                kind = excluded.kind
            """,
            (group["group_id"], group["name"], group.get("kind", "custom"), group["owner_id"], group["invite_code"]),
        )
        for uid in group.get("member_ids", []):
            self.join_group(group["group_id"], uid)

    def join_group(self, group_id: str, user_id: str) -> None:
        self._exec(
            """
            INSERT INTO group_members (group_id, user_id)
            VALUES (?, ?)
            ON CONFLICT (group_id, user_id) DO NOTHING
            """,
            (group_id, user_id),
        )

    def find_group_by_invite(self, invite_code: str) -> Optional[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM groups WHERE LOWER(invite_code) = LOWER(?)", (invite_code.strip(),))
        return rows[0] if rows else None

    def list_groups(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if user_id:
            groups = self._fetchall(
                """
                SELECT g.* FROM groups g
                JOIN group_members gm ON g.group_id = gm.group_id
                WHERE gm.user_id = ?
                ORDER BY g.name ASC
                """,
                (user_id,),
            )
        else:
            groups = self._fetchall("SELECT * FROM groups ORDER BY name ASC")
        for g in groups:
            members = self._fetchall("SELECT user_id FROM group_members WHERE group_id = ?", (g["group_id"],))
            g["member_ids"] = [m["user_id"] for m in members]
        return groups

    def upsert_space_meta(self, meta: Dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO spaces_meta (world_id, display_name, owner_id, audience_type, group_ids_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (world_id) DO UPDATE SET
                display_name = excluded.display_name,
                owner_id = excluded.owner_id,
                audience_type = excluded.audience_type,
                group_ids_json = excluded.group_ids_json
            """,
            (
                meta["world_id"],
                meta["display_name"],
                meta["owner_id"],
                meta.get("audience_type", "group"),
                json.dumps(meta.get("group_ids") or []),
            ),
        )

    def list_spaces_meta(self) -> List[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM spaces_meta ORDER BY display_name ASC")
        for r in rows:
            r["group_ids"] = json.loads(r.pop("group_ids_json", "[]") or "[]")
        return rows

    def upsert_zone(self, zone: Dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO spatial_zones (zone_id, world_id, name, x, y, z, radius_m, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (zone_id) DO UPDATE SET
                name = excluded.name,
                x = excluded.x,
                y = excluded.y,
                z = excluded.z,
                radius_m = excluded.radius_m
            """,
            (
                zone["zone_id"],
                zone["world_id"],
                zone["name"],
                float(zone["x"]),
                float(zone["y"]),
                float(zone["z"]),
                float(zone.get("radius_m", 1.2)),
                zone.get("created_by", "usr-conor"),
            ),
        )

    def list_zones(self, world_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if world_id:
            return self._fetchall("SELECT * FROM spatial_zones WHERE world_id = ? ORDER BY name ASC", (world_id,))
        return self._fetchall("SELECT * FROM spatial_zones ORDER BY name ASC")

    def delete_zone(self, zone_id: str) -> None:
        self._exec("DELETE FROM spatial_zones WHERE zone_id = ?", (zone_id,))

    def delete_object(self, object_id: str) -> None:
        self._exec("DELETE FROM spatial_objects WHERE id = ?", (object_id,))

    def delete_object_events_by_prefix(self, world_id: str, prefix: str) -> None:
        self._exec(
            "DELETE FROM object_events WHERE world_id = ? AND event_id LIKE ?",
            (world_id, f"{prefix}%"),
        )

    def clear_pins(self) -> None:
        self._exec("DELETE FROM spatial_pins")

    def clear_object_events(self) -> None:
        self._exec("DELETE FROM object_events")

    def append_object_event(self, event: Dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO object_events (
                event_id, object_id, label, world_id, event_type, actor_user_id, actor_name,
                from_zone, from_pos_json, to_zone, to_pos_json, summary, ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (event_id) DO UPDATE SET
                to_zone = excluded.to_zone,
                to_pos_json = excluded.to_pos_json,
                summary = excluded.summary,
                ts = excluded.ts
            """,
            (
                event["event_id"],
                event["object_id"],
                event["label"],
                event["world_id"],
                event["event_type"],
                event["actor_user_id"],
                event["actor_name"],
                event.get("from_zone", ""),
                json.dumps(event["from_pos"]) if event.get("from_pos") is not None else None,
                event.get("to_zone", ""),
                json.dumps(event["to_pos"]) if event.get("to_pos") is not None else None,
                event["summary"],
                float(event["timestamp"]),
            ),
        )

    def list_object_events(
        self,
        world_id: Optional[str] = None,
        object_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if world_id:
            clauses.append("world_id = ?")
            params.append(world_id)
        if object_id:
            clauses.append("object_id = ?")
            params.append(object_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = self._fetchall(f"SELECT * FROM object_events {where} ORDER BY ts ASC LIMIT ?", tuple(params))
        out = []
        for r in rows:
            out.append({
                "event_id": r["event_id"],
                "object_id": r["object_id"],
                "label": r["label"],
                "world_id": r["world_id"],
                "event_type": r["event_type"],
                "actor_user_id": r["actor_user_id"],
                "actor_name": r["actor_name"],
                "from_zone": r["from_zone"],
                "from_pos": json.loads(r["from_pos_json"]) if r.get("from_pos_json") else None,
                "to_zone": r["to_zone"],
                "to_pos": json.loads(r["to_pos_json"]) if r.get("to_pos_json") else None,
                "summary": r["summary"],
                "timestamp": float(r["ts"]),
            })
        return out

    def status(self) -> Dict[str, Any]:
        counts = {"spatial_objects": 0, "spatial_pins": 0, "world_snapshots": 0, "object_events": 0}
        for t in list(counts.keys()):
            rows = self._fetchall(f"SELECT COUNT(*) AS cnt FROM {t}")
            counts[t] = int(rows[0]["cnt"]) if rows else 0

        return {
            "backend": self.backend,
            "aiven_configured": bool(self.aiven_url),
            "connected": self.connected,
            "error": self.error,
            "rows": counts,
        }

    def close(self) -> None:
        if self._sqlite_conn is not None:
            self._sqlite_conn.close()
            self._sqlite_conn = None
        if self._pg_conn is not None:
            self._pg_conn.close()
            self._pg_conn = None
