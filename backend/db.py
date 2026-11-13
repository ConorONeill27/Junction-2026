"""
Persistence layer for WorldGit backed by Aiven PostgreSQL,
with automatic local SQLite fallback when AIVEN_DATABASE_URL is not configured.
"""
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class SpatialDatabase:
    def __init__(self) -> None:
        self.aiven_url: Optional[str] = os.getenv("AIVEN_DATABASE_URL")
        self.sqlite_path = Path(__file__).resolve().parent / "worldgit_aiven_cache.db"
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
                self._init_schema_pg()
                return
            except Exception as exc:
                self.error = str(exc)

        self._sqlite_conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self.backend = "sqlite-local-cache"
        self.connected = True
        self._init_schema_sqlite()

    def _init_schema_pg(self) -> None:
        assert self._pg_conn is not None
        with self._pg_conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS spatial_objects (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    z REAL NOT NULL,
                    seen_by TEXT NOT NULL,
                    ts DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS spatial_pins (
                    id TEXT PRIMARY KEY,
                    author TEXT NOT NULL,
                    text TEXT NOT NULL,
                    pin_type TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    z REAL NOT NULL,
                    landmark TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    age_label TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS world_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    ts DOUBLE PRECISION NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def _init_schema_sqlite(self) -> None:
        assert self._sqlite_conn is not None
        cur = self._sqlite_conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS spatial_objects (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                z REAL NOT NULL,
                seen_by TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS spatial_pins (
                id TEXT PRIMARY KEY,
                author TEXT NOT NULL,
                text TEXT NOT NULL,
                pin_type TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                z REAL NOT NULL,
                landmark TEXT NOT NULL,
                created_at REAL NOT NULL,
                age_label TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS world_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                ts REAL NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._sqlite_conn.commit()

    def upsert_object(self, obj: Dict[str, Any]) -> None:
        pos = obj["position"]
        params = (
            obj["id"],
            obj["label"],
            float(pos[0]),
            float(pos[1]),
            float(pos[2]),
            obj["seen_by"],
            float(obj["timestamp"]),
        )
        if self._pg_conn is not None:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spatial_objects (id, label, x, y, z, seen_by, ts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label,
                        x = EXCLUDED.x,
                        y = EXCLUDED.y,
                        z = EXCLUDED.z,
                        seen_by = EXCLUDED.seen_by,
                        ts = EXCLUDED.ts
                    """,
                    params,
                )
        elif self._sqlite_conn is not None:
            cur = self._sqlite_conn.cursor()
            cur.execute(
                """
                INSERT INTO spatial_objects (id, label, x, y, z, seen_by, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label = excluded.label,
                    x = excluded.x,
                    y = excluded.y,
                    z = excluded.z,
                    seen_by = excluded.seen_by,
                    ts = excluded.ts
                """,
                params,
            )
            self._sqlite_conn.commit()

    def upsert_pin(self, pin: Dict[str, Any]) -> None:
        pos = pin["position"]
        params = (
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
        )
        if self._pg_conn is not None:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spatial_pins
                        (id, author, text, pin_type, x, y, z, landmark, created_at, age_label)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        author = EXCLUDED.author,
                        text = EXCLUDED.text,
                        pin_type = EXCLUDED.pin_type,
                        x = EXCLUDED.x,
                        y = EXCLUDED.y,
                        z = EXCLUDED.z,
                        landmark = EXCLUDED.landmark,
                        created_at = EXCLUDED.created_at,
                        age_label = EXCLUDED.age_label
                    """,
                    params,
                )
        elif self._sqlite_conn is not None:
            cur = self._sqlite_conn.cursor()
            cur.execute(
                """
                INSERT INTO spatial_pins
                    (id, author, text, pin_type, x, y, z, landmark, created_at, age_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    author = excluded.author,
                    text = excluded.text,
                    pin_type = excluded.pin_type,
                    x = excluded.x,
                    y = excluded.y,
                    z = excluded.z,
                    landmark = excluded.landmark,
                    created_at = excluded.created_at,
                    age_label = excluded.age_label
                """,
                params,
            )
            self._sqlite_conn.commit()

    def save_snapshot(self, snapshot: Dict[str, Any]) -> None:
        params = (
            snapshot["snapshot_id"],
            snapshot["label"],
            float(snapshot["timestamp"]),
            json.dumps(snapshot["objects"]),
        )
        if self._pg_conn is not None:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_snapshots (snapshot_id, label, ts, payload_json)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (snapshot_id) DO UPDATE SET
                        label = EXCLUDED.label,
                        ts = EXCLUDED.ts,
                        payload_json = EXCLUDED.payload_json
                    """,
                    params,
                )
        elif self._sqlite_conn is not None:
            cur = self._sqlite_conn.cursor()
            cur.execute(
                """
                INSERT INTO world_snapshots (snapshot_id, label, ts, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    label = excluded.label,
                    ts = excluded.ts,
                    payload_json = excluded.payload_json
                """,
                params,
            )
            self._sqlite_conn.commit()

    def status(self) -> Dict[str, Any]:
        counts = {"spatial_objects": 0, "spatial_pins": 0, "world_snapshots": 0}
        tables: List[str] = list(counts.keys())
        if self._pg_conn is not None:
            with self._pg_conn.cursor() as cur:
                for t in tables:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    row = cur.fetchone()
                    counts[t] = int(row[0]) if row else 0
        elif self._sqlite_conn is not None:
            cur = self._sqlite_conn.cursor()
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                row = cur.fetchone()
                counts[t] = int(row[0]) if row else 0

        return {
            "backend": self.backend,
            "aiven_configured": bool(self.aiven_url),
            "connected": self.connected,
            "error": self.error,
            "rows": counts,
        }
