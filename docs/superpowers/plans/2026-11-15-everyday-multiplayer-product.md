# WorldGit Everyday Multiplayer Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform WorldGit from a single-user reconstruction workbench into a real-time multiplayer everyday spatial memory app where family members track moved items across named zones, friends share scoped notes & place reviews (`Public`, `Friends`, `Group`, `Specific People`, `Only Me`), and live/async glasses scans populate real 3D spaces.

**Architecture:** Additive social graph, zone resolution, object movement ledger (`placed`/`moved`/`seen`), per-viewer audience visibility filtering, and per-client `world_id` caching/WS routing inside `worldgit/backend/` (`db.py`, `social_store.py`, `main.py`, plus non-blocking bridges from `yolo_world/:8765`), paired with a modular Spatial UI / Apple-inspired consumer React + Three.js frontend in `worldgit/frontend/` (preserving all reconstruction/studio utilities inside a collapsible Studio drawer and keeping all `AGENTS.md` frozen contracts intact for `glasses-app/`).

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite + Aiven PostgreSQL (`psycopg`), React 18, Three.js (`0.170.0`), Vite 6, custom inline SVG icons + Spatial Glass CSS tokens (`Inter`).

**Spec:** [2026-11-15-everyday-multiplayer-product-design.md](file:///c:/Users/conor/Documents/Junction%202026/worldgit/docs/superpowers/specs/2026-11-15-everyday-multiplayer-product-design.md)

## Global Constraints

- **No fake synthetic 3D worlds:** Operate only on real scanned spaces (`world_1` and newly uploaded/scanned videos/photos). Unmapped outdoor place reviews (e.g., `Subway`) live cleanly in **Notes & Places** with optional `lat`/`lon` fields ready for future Google Earth 3D Tiles.
- **Frozen `AGENTS.md` contracts:** Preserve all existing JSON keys & types on `GET /api/state`, `POST /api/ctrl-f`, `GET /api/ctrl-z`, `POST /api/pin`, `POST /api/analyze-frame`, `POST /api/tts`, and `WS /ws/stream`; only add optional fields.
- **Zero-key offline-first resilience:** Startup and all endpoints work with SQLite fallback (`worldgit_aiven_cache.db`), no `GEMINI_API_KEY`, no `ELEVENLABS_API_KEY`, and even when `:8000` or `:8765` peer processes are offline.
- **Strict per-viewer privacy filtering:** When `viewer_id` is provided on REST or WS, never leak `private`, `people`, or non-member `group` spaces/objects/timelines/pins. Omitted `viewer_id` defaults to `"usr-conor"` (`wearer-b`) for glasses backward-compatibility; `"guest"` sees `"public"` only.
- **No industrial placeholders in everyday mode:** Migrate legacy `obj-drill`, `obj-socket`, `obj-medkit`, `obj-battery`, `obj-laser`, `LIVE 400V PANEL`, `Breaker Panel`, `drywall boxes` cleanly out of active consumer state while preserving real user records and localized `Water Bottle` (`[-0.719, 0.055, 0.697]`).
- **UI Design System (`Apple` + `Spatial UI`):** Dark obsidian 3D canvas (`#060913`), frosted dark glass panels (`rgba(16, 21, 34, 0.84)`, hairline `rgba(255,255,255,0.12)`), Action Blue `#2997ff` / `#0071e3`, Emerald `#34d399` for confirmed locations, `Inter` font, **SVG icons only (zero emojis as UI icons)**, `≥44×44px` interactive targets on touch/key controls, `150–250ms` transitions, `prefers-reduced-motion` support, responsive at `390px` and `1440px`.

## Review Focus

1. **False "Moved" Event on Re-Sighting:** Another family member walking past an unmoved `Charger` at `Sitting Room Press` (`displacement < 0.35m` and same zone) must log `"seen"` only — never `"Conor moved Charger from Sitting Room Press to Sitting Room Press"`. *(Pinned in Task 1 test suite)*
2. **Audience Leakage Across Profile Switch / Multi-Tab WS:** A pin scoped to `grp-family` (`Dad`) or `target_user_ids=["usr-mum"]` must never appear in `GET /api/state?viewer_id=usr-alex`, `GET /api/state?viewer_id=guest`, or on Alex's `/ws/stream` push. *(Pinned in Task 2 test suite)*
3. **Per-Tab Space Isolation (`world_id`):** Tab A requesting `GET /api/state?world_id=world_1` vs Tab B creating/viewing a second space must not overwrite each other's active `points.bin` or WS feed. *(Pinned in Task 2 test suite)*
4. **Migration Idempotency on Existing SQLite Cache:** Starting up against an existing `worldgit_aiven_cache.db` that contains both old `pin-1` (`LIVE 400V PANEL`) and real user-created `pin-water-bottle` / `pin-custom` must remove legacy industrial rows without deleting user pins. *(Pinned in Task 1 test suite)*
5. **Graceful `:8765` → `:8000` Bridge Failure Isolation:** Creating a `Subway` review on `:8765` (`PlaceAnchorStore`) or detecting a bottle when `:8000` is stopped or slow must still succeed locally on `:8765` within `<200ms` without raising an exception. *(Pinned in Task 3 test suite)*

---

### File Map

- Create: `worldgit/backend/social_store.py` — Domain helpers for visibility checks, zone resolution, `wearer_id`↔`user_id` aliasing, movement transition logic (`placed`/`moved`/`seen`), natural QA answer formatting, and everyday default seeds.
- Modify: `worldgit/backend/db.py` — Add social, space-meta, zone, and `object_events` tables (PostgreSQL + SQLite), additive columns on `spatial_pins` and `spatial_objects`, CRUD queries, and idempotent legacy seed purge.
- Modify: `worldgit/backend/main.py` — Per-world loaded point-cloud cache, async `scan_jobs` status tracking, per-socket `(viewer_id, world_id)` WS filtering, new `/api/social/*`, `/api/zones`, `/api/objects/sighting`, `/api/objects/history`, `/api/ask`, `/api/spaces/share`, and extended `/api/pin` & `/api/ctrl-f`.
- Modify: `worldgit/backend/yolo_world/detection_server.py`, `place_anchors.py`, `spatial_notes.py`, `object_memory.py` — Non-blocking `:8765` → `:8000` forwarding for reviews (`Subway`), notes, and sightings with audience metadata & optional `lat`/`lon`.
- Modify: `worldgit/frontend/index.html` — Page title + Inter Google Font link.
- Create: `worldgit/frontend/src/styles.css` — Spatial UI / Apple glass tokens, responsive breakpoints, focus rings, `prefers-reduced-motion`.
- Create: `worldgit/frontend/src/components/Icons.jsx` — Clean 18px/20px inline SVG icon library (zero emoji chrome).
- Create: `worldgit/frontend/src/hooks/useMultiplayerSpace.js` — Per-tab `viewerId` / `worldId` state, `/ws/stream` client, REST actions (ask, friends, groups, zones, pins, scan jobs).
- Create: `worldgit/frontend/src/components/TopNav.jsx` — Spaces switcher + scan job pill, `"Ask Your Spaces"` bar with TTS button, active profile switcher, Studio drawer toggle.
- Create: `worldgit/frontend/src/components/SideDrawer.jsx` — 3 consumer tabs (`Things & History`, `Notes & Places`, `People & Groups`).
- Create: `worldgit/frontend/src/components/NoteComposerModal.jsx` — Scoped Note / Place Review composer (`Public` / `Friends` / `Group(s)` / `Specific People` / `Only Me`, star rating, optional coordinates/space).
- Create: `worldgit/frontend/src/components/ClickActionPopover.jsx` — Floating 3D-click popover (`Drop Note Here` or `Name This Zone`).
- Create: `worldgit/frontend/src/components/StudioDrawer.jsx` — Secondary collapsible `"Map & Scan Studio"` drawer keeping all VGGT / `Locate Video on 3D Map` / `Focus Bottle` / `.mp4` upload / visualization sliders intact.
- Modify: `worldgit/frontend/src/App.jsx` & `worldgit/frontend/src/main.jsx` — Compose Three.js viewport (wearer avatars, audience-badged pins, item beacons, dashed From→To movement arcs, 3D raycast click picker) with the new consumer components.
- Create: `worldgit/backend/test_social_store.py`, `worldgit/backend/test_api_multiplayer.py`, `worldgit/backend/yolo_world/test_studio_bridge.py`.

---

### Task 1: Social Graph, Named Zones, Visibility & Movement Ledger (`backend/social_store.py` + `backend/db.py`)

**Files:**
- Create: `worldgit/backend/social_store.py`
- Modify: `worldgit/backend/db.py`
- Test: `worldgit/backend/test_social_store.py`

**Interfaces:**
- Produces in `worldgit/backend/social_store.py`:
  - `LEGACY_INDUSTRIAL_OBJECT_IDS: set[str]` & `LEGACY_INDUSTRIAL_PIN_IDS: set[str]`
  - `WEARER_TO_USER: dict[str, str]` & `USER_TO_WEARER: dict[str, str]`
  - `resolve_viewer_id(viewer_id: Optional[str]) -> str` (`None` -> `"usr-conor"`, stripped lowercase `"guest"` -> `"guest"`)
  - `can_user_view(record: dict, viewer_id: Optional[str], db: SpatialDatabase) -> bool`
  - `resolve_zone(world_id: str, position: list[float], zones: list[dict], placement_hint: Optional[str] = None) -> str`
  - `evaluate_sighting_transition(prev_obj: Optional[dict], prev_zone: Optional[str], new_pos: list[float], new_zone: str, actor_user_id: str, actor_name: str, label: str, object_id: str, world_id: str, ts: float) -> tuple[str, Optional[dict]]` (returns `("placed"|"moved"|"seen", event_dict_or_None)`)
  - `build_ask_answer(item_label: str, current_zone: str, distance_m: float, turn: str, events: list[dict]) -> str`
  - `seed_everyday_social_data(db: SpatialDatabase) -> None`
- Produces in `worldgit/backend/db.py`:
  - `SpatialDatabase` new SQLite/PG tables + helper methods: `list_users()`, `upsert_user(user)`, `get_user(user_id)`, `add_friendship(user_a, user_b)`, `list_friend_ids(user_id)`, `list_groups(user_id=None)`, `create_group(group)`, `join_group(group_id, user_id)`, `find_group_by_invite(invite_code)`, `upsert_space_meta(meta)`, `list_spaces_meta()`, `upsert_zone(zone)`, `list_zones(world_id=None)`, `append_object_event(event)`, `list_object_events(world_id=None, object_id=None, limit=100)`, `list_objects(world_id=None)`, `list_pins(world_id=None)`, `migrate_purge_legacy_industrial_seed()`.

- [ ] **Step 1: Write failing tests in `worldgit/backend/test_social_store.py`**

```python
import tempfile
import unittest
from pathlib import Path
from db import SpatialDatabase
from social_store import (
    can_user_view,
    evaluate_sighting_transition,
    resolve_zone,
    seed_everyday_social_data,
)


class SocialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SpatialDatabase(sqlite_path=Path(self.tmp.name) / "test.db")
        seed_everyday_social_data(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_visibility_scopes_and_guest_isolation(self):
        fam_pin = {"author_id": "usr-mum", "audience_type": "group", "group_ids": ["grp-family"], "target_user_ids": []}
        priv_pin = {"author_id": "usr-conor", "audience_type": "private", "group_ids": [], "target_user_ids": []}
        friend_pin = {"author_id": "usr-alex", "audience_type": "friends", "group_ids": [], "target_user_ids": []}
        direct_pin = {"author_id": "usr-conor", "audience_type": "people", "group_ids": [], "target_user_ids": ["usr-sarah"]}
        pub_pin = {"author_id": "usr-alex", "audience_type": "public", "group_ids": [], "target_user_ids": []}

        self.assertTrue(can_user_view(fam_pin, "usr-conor", self.db))
        self.assertFalse(can_user_view(fam_pin, "usr-alex", self.db))
        self.assertFalse(can_user_view(priv_pin, "usr-mum", self.db))
        self.assertTrue(can_user_view(friend_pin, "usr-conor", self.db))
        self.assertFalse(can_user_view(friend_pin, "guest", self.db))
        self.assertTrue(can_user_view(direct_pin, "usr-sarah", self.db))
        self.assertFalse(can_user_view(direct_pin, "usr-alex", self.db))
        self.assertTrue(can_user_view(pub_pin, "guest", self.db))

    def test_moved_vs_seen_prevents_false_moves(self):
        ev_type, ev = evaluate_sighting_transition(
            prev_obj={"position": [0.0, 0.8, 0.0], "seen_by": "Conor"},
            prev_zone="Kitchen Table",
            new_pos=[0.05, 0.8, 0.05],
            new_zone="Kitchen Table",
            actor_user_id="usr-mum",
            actor_name="Mum",
            label="Phone Charger",
            object_id="obj-charger",
            world_id="world_1",
            ts=100.0,
        )
        self.assertEqual(ev_type, "seen")
        self.assertIsNone(ev)

        ev_type2, ev2 = evaluate_sighting_transition(
            prev_obj={"position": [0.0, 0.8, 0.0], "seen_by": "Conor"},
            prev_zone="Kitchen Table",
            new_pos=[2.4, 0.9, 1.2],
            new_zone="Sitting Room Press",
            actor_user_id="usr-mum",
            actor_name="Mum",
            label="Phone Charger",
            object_id="obj-charger",
            world_id="world_1",
            ts=200.0,
        )
        self.assertEqual(ev_type2, "moved")
        self.assertIn("Kitchen Table", ev2["from_zone"])
        self.assertIn("Sitting Room Press", ev2["to_zone"])
        self.assertEqual(ev2["actor_name"], "Mum")

    def test_zone_resolution_and_legacy_purge_keeps_user_pins(self):
        self.db.upsert_pin({"id": "pin-1", "author": "Alex", "text": "LIVE 400V PANEL", "pin_type": "warning", "position": [0, 1, 0], "landmark": "Breaker Panel", "created_at": 1.0, "age_label": "1m"})
        self.db.upsert_pin({"id": "pin-user-custom", "author": "Conor", "text": "Spare key here", "pin_type": "note", "position": [1, 1, 1], "landmark": "Hall", "created_at": 2.0, "age_label": "now"})
        self.db.migrate_purge_legacy_industrial_seed()
        pin_ids = {p["id"] for p in self.db.list_pins()}
        self.assertNotIn("pin-1", pin_ids)
        self.assertIn("pin-user-custom", pin_ids)

        zones = [{"zone_id": "z1", "world_id": "world_1", "name": "Beside Left Bed", "x": -0.7, "y": 0.1, "z": 0.7, "radius_m": 1.0}]
        self.assertEqual(resolve_zone("world_1", [-0.72, 0.06, 0.70], zones, "floor"), "Beside Left Bed")
        self.assertEqual(resolve_zone("world_1", [5.0, 0.0, 5.0], zones, "beside the window"), "beside the window")
```

- [ ] **Step 2: Run test to verify it fails**

Run in `worldgit/backend`: `.\.venv311\Scripts\python.exe -m unittest test_social_store.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'social_store'`)

- [ ] **Step 3: Implement `SpatialDatabase` extensions in `worldgit/backend/db.py` (accept optional `sqlite_path: Optional[Path] = None` in `__init__` for isolated unit tests, add SQLite + PG schema & `SELECT`/CRUD methods + `migrate_purge_legacy_industrial_seed()`) and create `worldgit/backend/social_store.py`**

- [ ] **Step 4: Run test to verify it passes**

Run in `worldgit/backend`: `.\.venv311\Scripts\python.exe -m unittest test_social_store.py -v`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/social_store.py backend/test_social_store.py
git commit -m "feat: add social graph, zones, object movement ledger, and DB migration"
```

---

### Task 2: Multi-Space Backend APIs, Visibility Filtering, `/api/ask`, Async Scan Jobs & Per-Client WS (`backend/main.py`)

**Files:**
- Modify: `worldgit/backend/main.py`
- Test: `worldgit/backend/test_api_multiplayer.py`

**Interfaces:**
- Consumes Task 1 (`db.py`, `social_store.py`).
- Produces on `:8000`:
  - `GET /api/social/bootstrap?viewer_id=...` -> `{viewer, users, friends, groups, friend_groups_only, spaces, scan_jobs}`
  - `POST /api/social/profiles` (`{name, handle}`) -> creates profile + wearer mapping
  - `POST /api/social/friends/add` (`{user_id, target_handle_or_code}`) -> adds symmetric friendship
  - `POST /api/social/groups` (`{owner_id, name, kind, member_ids}`) & `POST /api/social/groups/join` (`{user_id, invite_code}`)
  - `POST /api/spaces/share` (`{world_id, viewer_id, display_name, audience_type, group_ids}`)
  - `GET /api/zones?world_id=...` & `POST /api/zones` (`{world_id, name, position, radius_m, created_by}`)
  - `POST /api/objects/sighting` (`{world_id, label, position, actor_user_id, zone_hint}`) -> returns `{object, event_type, event}`
  - `GET /api/objects/history?world_id=...&query=...&viewer_id=...` -> `{events}`
  - `POST /api/ask` (`{viewer_id, wearer_id, query, world_id}`) -> `{found, world_id, space_name, object, history, path, distance_m, bearing_deg, arrow, spoken_answer, directions}`
  - Extended `GET /api/state?viewer_id=...&world_id=...`, `GET /api/points.bin?world_id=...`, `POST /api/pin` (additive `world_id, author_id, audience_type, group_ids, target_user_ids, rating, place_name, lat, lon`), `WS /ws/stream?viewer_id=...&world_id=...`.

- [ ] **Step 1: Write failing API tests in `worldgit/backend/test_api_multiplayer.py`**

```python
import unittest
from fastapi.testclient import TestClient
import main as backend_main


class MultiplayerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(backend_main.app)

    def test_scoped_pin_filtering_by_viewer(self):
        res = self.client.post("/api/pin", json={
            "author": "Dad",
            "author_id": "usr-dad",
            "text": "Spare house key is in hall drawer",
            "pin_type": "note",
            "position": [0.2, 0.9, -0.4],
            "landmark": "Hall Drawer",
            "audience_type": "group",
            "group_ids": ["grp-family"],
        })
        self.assertEqual(res.status_code, 200)
        pid = res.json()["pin"]["id"]

        conor_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=usr-conor").json()["pins"]}
        alex_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=usr-alex").json()["pins"]}
        guest_pins = {p["id"] for p in self.client.get("/api/state?viewer_id=guest").json()["pins"]}
        self.assertIn(pid, conor_pins)
        self.assertNotIn(pid, alex_pins)
        self.assertNotIn(pid, guest_pins)

    def test_mum_moves_charger_flow_and_ask_qa(self):
        # 1. Name two zones in active space
        self.client.post("/api/zones", json={"world_id": "world_1", "name": "Kitchen Table", "position": [0.5, 0.8, 0.2], "radius_m": 0.9, "created_by": "usr-conor"})
        self.client.post("/api/zones", json={"world_id": "world_1", "name": "Press in Sitting Room", "position": [-0.8, 0.8, -0.9], "radius_m": 0.9, "created_by": "usr-mum"})

        # 2. Conor leaves charger at Kitchen Table
        r1 = self.client.post("/api/objects/sighting", json={
            "world_id": "world_1", "label": "Phone Charger", "position": [0.5, 0.8, 0.2], "actor_user_id": "usr-conor", "zone_hint": "table"
        }).json()
        self.assertEqual(r1["event_type"], "placed")

        # 3. Mum moves it to Press in Sitting Room
        r2 = self.client.post("/api/objects/sighting", json={
            "world_id": "world_1", "label": "Phone Charger", "position": [-0.8, 0.8, -0.9], "actor_user_id": "usr-mum", "zone_hint": "cabinet"
        }).json()
        self.assertEqual(r2["event_type"], "moved")

        # 4. Ask where charger is
        ask = self.client.post("/api/ask", json={"viewer_id": "usr-conor", "wearer_id": "wearer-b", "query": "Where is my charger?"}).json()
        self.assertTrue(ask["found"])
        self.assertIn("Kitchen Table", ask["spoken_answer"])
        self.assertIn("Press in Sitting Room", ask["spoken_answer"])
        self.assertIn("Mum", ask["spoken_answer"])

    def test_legacy_contracts_remain_valid(self):
        s = self.client.get("/api/state").json()
        for k in ("wearers", "objects", "pins", "snapshots", "diff", "point_count"):
            self.assertIn(k, s)
        cz = self.client.get("/api/ctrl-z").json()
        self.assertIn("diff", cz)
```

- [ ] **Step 2: Run test to verify it fails**

Run in `worldgit/backend`: `.\.venv311\Scripts\python.exe -m unittest test_api_multiplayer.py -v`
Expected: FAIL (`404 Not Found` on `/api/zones`, `/api/objects/sighting`, `/api/ask`)

- [ ] **Step 3: Implement world caching (`_load_world_data_cached`), per-socket WS viewer/world filtering, `scan_jobs` tracking in `/api/dat-scans` & `/api/reconstruct-video-bytes`, and new endpoints in `worldgit/backend/main.py` (preserving legacy `world.objects` Hydration — never wiping localized room bottle on startup!)**

- [ ] **Step 4: Run tests to verify they pass**

Run in `worldgit/backend`: `.\.venv311\Scripts\python.exe -m unittest test_api_multiplayer.py test_social_store.py test_video_localizer.py test_photos_to_video.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/test_api_multiplayer.py
git commit -m "feat: add multi-viewer visibility filtering, zones, movement QA, and per-space WS routing"
```

---

### Task 3: Bridge `:8765` (`yolo_world`) Reviews & Sightings to `:8000`

**Files:**
- Modify: `worldgit/backend/yolo_world/place_anchors.py`
- Modify: `worldgit/backend/yolo_world/spatial_notes.py`
- Modify: `worldgit/backend/yolo_world/object_memory.py`
- Modify: `worldgit/backend/yolo_world/detection_server.py`
- Test: `worldgit/backend/yolo_world/test_studio_bridge.py`

**Interfaces:**
- Consumes Task 2 `:8000` `/api/pin` and `/api/objects/sighting`.
- Produces non-blocking `post_json_async(url, payload)` helper in `detection_server.py` (`daemon=True` thread, `timeout=1.5s`, ignores connection errors silently) so:
  - `POST /api/places` forwards `{author, author_id, text, pin_type: "friend_memory", landmark, place_name, rating, audience_type, group_ids, target_user_ids, lat, lon, world_id}` to `/api/pin`.
  - `POST /api/notes` includes optional `author_id`, `audience_type`, `group_ids`, `target_user_ids`, `world_id`.
  - `POST /api/objects/sighting` receives settled personal item detections from `ObjectMemoryStore` with spatial phrase as `zone_hint`.

- [ ] **Step 1: Write failing test in `worldgit/backend/yolo_world/test_studio_bridge.py`**

```python
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import tempfile
import unittest
from pathlib import Path
import numpy as np
from place_anchors import PlaceAnchorStore


class StudioBridgeTests(unittest.TestCase):
    def test_place_review_forwards_scoped_pin_nonblocking_and_handles_offline(self):
        received = []

        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length", "0"))
                received.append((self.path, json.loads(self.rfile.read(ln).decode())))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                store = PlaceAnchorStore(Path(d), studio_url=f"http://127.0.0.1:{srv.server_port}")
                img = np.full((120, 120, 3), 180, dtype=np.uint8)
                store.add_place("Subway", 5, "Great Italian BMT", author="Alex", image_bgr=img, audience_type="friends", group_ids=["grp-otaniemi"], lat=60.184, lon=24.830)
                for _ in range(20):
                    if received:
                        break
                    threading.Event().wait(0.05)
                self.assertEqual(len(received), 1)
                path, body = received[0]
                self.assertEqual(path, "/api/pin")
                self.assertEqual(body["place_name"], "Subway")
                self.assertEqual(body["audience_type"], "friends")
                self.assertEqual(body["lat"], 60.184)

                # Offline studio does not raise or stall
                off_store = PlaceAnchorStore(Path(d) / "off", studio_url="http://127.0.0.1:1")
                rec = off_store.add_place("Subway", 4, "Still works offline", author="Alex", image_bgr=img)
                self.assertEqual(rec["name"], "Subway")
        finally:
            srv.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run in `worldgit/backend/yolo_world`: `..\.venv311\Scripts\python.exe -m unittest test_studio_bridge.py -v`
Expected: FAIL (`TypeError: PlaceAnchorStore.__init__() got an unexpected keyword argument 'studio_url'`)

- [ ] **Step 3: Add `studio_url` + non-blocking forwarding + optional audience/`lat`/`lon` parameters in `place_anchors.py`, `spatial_notes.py`, `object_memory.py`, and `detection_server.py`**

- [ ] **Step 4: Run test + full `yolo_world` test suite**

Run in `worldgit/backend/yolo_world`: `..\.venv311\Scripts\python.exe -m unittest discover -s . -p "test_*.py" -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/yolo_world/place_anchors.py backend/yolo_world/spatial_notes.py backend/yolo_world/object_memory.py backend/yolo_world/detection_server.py backend/yolo_world/test_studio_bridge.py
git commit -m "feat: bridge scoped place reviews and sightings from :8765 to :8000"
```

---

### Task 4: Design System Tokens, SVG Icons & Multiplayer Client Hook (`frontend/`)

**Files:**
- Modify: `worldgit/frontend/index.html`
- Create: `worldgit/frontend/src/styles.css`
- Create: `worldgit/frontend/src/components/Icons.jsx`
- Create: `worldgit/frontend/src/hooks/useMultiplayerSpace.js`
- Modify: `worldgit/frontend/src/main.jsx`

**Interfaces:**
- Consumes Task 2 APIs (`/api/social/bootstrap`, `/api/state`, `/api/ask`, `/api/pin`, `/api/zones`, `/api/objects/sighting`, `/api/objects/history`, `/api/social/*`, `/ws/stream`).
- Produces:
  - `worldgit/frontend/src/styles.css`: CSS variables implementing **Spatial UI (VisionOS) + Apple** (`--bg-canvas: #050811`, `--glass-surface: rgba(17, 22, 34, 0.84)`, `--hairline: rgba(255, 255, 255, 0.12)`, `--accent-blue: #2997ff`, `--accent-emerald: #34d399`, `--accent-amber: #f59e0b`, `44px` `.touch-btn`, `prefers-reduced-motion`, mobile drawer media rules).
  - `Icons.jsx`: Clean inline SVG components (`HomeIcon`, `SearchIcon`, `UsersIcon`, `MapPinIcon`, `TagIcon`, `VolumeIcon`, `SlidersIcon`, `PlusIcon`, `LockIcon`, `GlobeIcon`, `HistoryIcon`, `VideoIcon`, `CrosshairIcon`). Zero emojis anywhere.
  - `useMultiplayerSpace()`: hook managing per-tab `viewerId` & `worldId` (`URLSearchParams` + `sessionStorage`), WS live updates, `askSpace(query)`, `addFriend(handleOrCode)`, `createGroup(name, memberIds)`, `joinGroup(inviteCode)`, `createZone(name, pos)`, `dropScopedPin(payload)`, `recordSighting(payload)`, `openSecondPlayerTab()`.

- [ ] **Step 1: Add `Inter` Google Font + clean title `"WorldGit — Shared Everyday Spaces"` in `frontend/index.html` and create `frontend/src/styles.css` + `frontend/src/components/Icons.jsx`**
- [ ] **Step 2: Import `./styles.css` in `frontend/src/main.jsx` and implement `frontend/src/hooks/useMultiplayerSpace.js`**
- [ ] **Step 3: Verify `npm run build` compiles cleanly with zero errors**

Run in `worldgit/frontend`: `npm run build`
Expected: `✓ built` exit code `0`

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/src/main.jsx frontend/src/styles.css frontend/src/components/Icons.jsx frontend/src/hooks/useMultiplayerSpace.js
git commit -m "feat: add Spatial UI design tokens, SVG icons, and multiplayer state hook"
```

---

### Task 5: Consumer Header, Ask Bar, Live 3D Avatars, Movement Arcs & Click-to-Pin/Zone Popover (`frontend/`)

**Files:**
- Create: `worldgit/frontend/src/components/TopNav.jsx`
- Create: `worldgit/frontend/src/components/ClickActionPopover.jsx`
- Create: `worldgit/frontend/src/components/StudioDrawer.jsx`
- Modify: `worldgit/frontend/src/App.jsx`

**Interfaces:**
- Consumes Task 4 `Icons.jsx`, `styles.css`, `useMultiplayerSpace()`.
- Features in 3D Viewport & Header:
  - `TopNav.jsx`: Space selector + background scan job progress badge, `"Ask where something is..."` form + TTS audio playback badge, Profile switcher (`Conor`, `Mum`, `Dad`, `Alex`, `Sarah`, `Guest (Public)`) + `"Open Friend Tab"` button (opens new tab with `?user=usr-mum&space=...`) + `"Map & Scan Studio"` toggle button.
  - `ClickActionPopover.jsx`: Clicking the 3D point cloud / floor casts a ray onto the nearest point cluster and lets you immediately **Name Zone** (e.g. `"Sitting Room Press"`) or **Drop Scoped Note** (`[x, y, z]`) at that exact spot.
  - `StudioDrawer.jsx`: Houses all existing reconstruction & test buttons (`Locate Video on 3D Map`, `Focus Bottle`, `Load VGGT Walk-Around`, `Upload .MP4 (VGGT)`, `Clear / Reset 3D Map`, `Point Cloud`, `AI Surface Fill`, `Architectural Shell`, `Point Size`, `Camera Path`, `Video Pins`, `Auto-Turntable`).
  - `App.jsx`: Renders **Live Multiplayer Avatars** (colored beacons + name sprites for `wearers`), **Audience-badged 3D Pins**, **Dashed From→To Movement Arc** for queried/selected moved items, and preserves `?mode=scan` phone scanner cleanly.

- [ ] **Step 1: Implement `TopNav.jsx`, `ClickActionPopover.jsx`, and `StudioDrawer.jsx`**
- [ ] **Step 2: Wire `App.jsx` with raycast 3D click selection, live wearer avatars, From→To movement arc rendering, and TopNav + StudioDrawer**
- [ ] **Step 3: Run `npm run build` in `worldgit/frontend`**

Expected: `✓ built` exit code `0`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/TopNav.jsx frontend/src/components/ClickActionPopover.jsx frontend/src/components/StudioDrawer.jsx frontend/src/App.jsx
git commit -m "feat: add consumer top nav, 3D click-to-zone/note picker, live avatars, and movement arcs"
```

---

### Task 6: Slide-Over Consumer Drawer (Things & History, Notes & Places, People & Groups) + End-to-End Verification

**Files:**
- Create: `worldgit/frontend/src/components/NoteComposerModal.jsx`
- Create: `worldgit/frontend/src/components/SideDrawer.jsx`
- Modify: `worldgit/frontend/src/App.jsx`

**Interfaces:**
- `NoteComposerModal.jsx`: Scoped note / restaurant review composer with audience radio pills (`Public`, `All Friends`, `Group(s)` checklist, `Specific People` checklist, `Only Me`), 1–5 star rating toggle for places, optional place name & GPS tag badge (`lat/lon`).
- `SideDrawer.jsx` (3 consumer tabs, responsive left drawer on desktop / collapsible sheet on `390px` mobile):
  - **Things & History:** Active items with current resolved zone, quick `"Check Left Behind"` button, full chronological movement timeline (`"Mum moved Phone Charger from Kitchen Table → Sitting Room Press"`), 1-click camera focus + movement arc highlight.
  - **Notes & Places:** All pins & restaurant reviews visible to active user (`Subway`, `Corner Espresso Bar`, family reminders) with audience pill badge, rating stars, `+ Add Note / Review` trigger, and 1-click fly-to-3D if attached to the active space.
  - **People & Groups:** Live active users in current space (plus interactive `"Move Avatar to Zone"` / `"Record Sighting"` control for multi-tab walkthrough testing), **Friends** list + `"Add Friend by Handle or Code"`, **Groups** list + `"Create Group"` / `"Join by Invite Code"`, and **Spaces & Async Scans** (`Upload .mp4` scan with live background job progress).

- [ ] **Step 1: Implement `NoteComposerModal.jsx` and `SideDrawer.jsx` and connect into `App.jsx`**
- [ ] **Step 2: Run `npm run build` in `worldgit/frontend`**

Expected: `✓ built` exit code `0`

- [ ] **Step 3: Run all backend unit tests + `yolo_world` unit tests + `glasses-app` static validators**

Run:
- `worldgit/backend`: `.\.venv311\Scripts\python.exe -m unittest test_social_store.py test_api_multiplayer.py test_video_localizer.py test_photos_to_video.py`
- `worldgit/backend/yolo_world`: `..\.venv311\Scripts\python.exe -m unittest discover -s . -p "test_*.py"`
- `worldgit/glasses-app`: `npm run build` + `validate-ui-toolkit.mjs` + `check-webapp.mjs --static-only`
Expected: Zero failures, zero UI-toolkit findings, `"passed": true`

- [ ] **Step 4: Live Multi-User Browser Verification (`http://localhost:5173/`) via `browser` subagent**

Verify:
1. Default consumer view shows `Room Scan` + `Water Bottle` (`[-0.719, 0.055, 0.697]`), zero industrial labels (`Red DeWalt Drill`, `LIVE 400V PANEL`), zero console/network errors, zero HUD overlap.
2. Family move loop works end-to-end: Click 3D space -> Name zone `"Kitchen Table"` & `"Sitting Room Press"`, switch between `Conor` and `Mum`, record `Phone Charger` placed at `Kitchen Table` -> moved by `Mum` to `Sitting Room Press`, ask `"Where is my charger?"` -> speaks/displays natural `From → To` answer and renders the 3D movement arc.
3. Scoped privacy filtering works: Note scoped to `Family` (`grp-family`) shows for `Conor` & `Mum`, disappears immediately when switching profile to `Alex` or `Guest (Public)`; restaurant review (`Subway`) scoped to `Friends` / `Otaniemi Foodies` shows for `Conor` & `Alex` and hides from `Guest`.
4. Secondary `"Map & Scan Studio"` drawer opens cleanly and `Focus Bottle` + `Locate Video on 3D Map` still work.

- [ ] **Step 5: Final commit & push**

```bash
git add frontend/src/components/NoteComposerModal.jsx frontend/src/components/SideDrawer.jsx frontend/src/App.jsx
git commit -m "feat: complete everyday multiplayer spatial memory UI and verification"
git push origin main
```
