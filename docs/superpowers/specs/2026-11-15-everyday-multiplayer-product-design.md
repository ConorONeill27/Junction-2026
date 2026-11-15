# WorldGit Everyday Multiplayer Product Design

**Date:** 2026-11-15
**Status:** Ready for Approval (Builder Check Completed & Resolved)
**Chosen Approach:** Approach A — Consumer Product Experience in `worldgit/frontend/` backed by Multi-Space, Movement Ledger, Async Scanning, and Friend/Group Scoped Permissions in `worldgit/backend/`

---

## 1. Intent & Why

Turn WorldGit from an internal 3D reconstruction & object-detection workbench into a **real, working everyday consumer spatial-memory product** for families, roommates, and friends.

Two core daily loops that work live on any scanned 3D space:
1. **Family / Household Shared Spatial Memory:** Anyone wearing glasses (or carrying a phone) passively maps shared spaces and streams detections/sightings. If you leave an item somewhere (e.g., charger on the kitchen table, water bottle beside the bed) and someone else in the household moves it somewhere else (e.g., sitting room press), the backend records the transition and answers *"Where is my charger?"* naturally via voice, glasses HUD, and 3D map movement path. Scanning/reconstruction runs asynchronously in the background so scanning can take whatever processing time it needs while live multiplayer features stay responsive.
2. **Social Spatial Notes, Place Reviews & Scoped Sharing:** Users add friends, form groups (`Family`, `Roommates`, custom friend groups), and publish notes or restaurant/place reviews (`Subway`, etc.) anchored either inside a 3D space or as visual place anchors, with strict audience visibility: `Private`, `Specific People`, `Group(s)`, `All Friends`, or `Public`.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Real Product Architecture (No Fake Synthetic Worlds):** Do not fabricate fake synthetic houses or demo restaurant point clouds. Everything works natively on real scanned 3D spaces (`world_1` on disk + any newly captured video or glasses live scan processed asynchronously into `MultiWorldManager`).
- **Consumer-first UI (`worldgit/frontend/`):** Everyday product interface organized around **Spaces**, **Ask Your Spaces ("Where is my...?")**, **Item Movement History**, **Scoped Notes & Places**, and **Friends / Groups / Live People**, while keeping raw reconstruction tools (`Locate Video on 3D Map`, `Load VGGT`, `Upload .MP4`, `AI Surface Fill`, `Point Size`, `Clear`) neatly tucked inside a secondary `"Map & Scan Studio"` drawer.
- **Live Glasses / Video Scanning + Async Processing:** Support triggering/uploading glasses live scans (`/api/dat-scans`, `/api/reconstruct-video-bytes`) with visible background processing status (`"Scanning / Processing in background..."`) so a user can keep browsing spaces, checking items, and posting notes while VGGT/multi-world stitching runs.
- **Named Zones + Object Movement Ledger:** Let members name 3D zones inside any mapped space (by clicking a 3D spot to create `"Kitchen Table"`, `"Sitting Room Press"`, `"Beside Left Bed"`, etc., plus automatic fallback to visual placement hints), and track **real item moves vs passive re-sightings** across household members.
- **Server-Enforced Audience Visibility:** Strictly filter spaces, objects, movement timelines, and pins per `viewer_id` on both REST and per-connection WebSocket streams.
- **Legacy Compatibility (`AGENTS.md`):** Preserve 100% of frozen response fields for `glasses-app/` and `android-dat-recorder/`, and preserve offline zero-API-key fallbacks (`db.py` SQLite + Aiven Postgres).

### Non-Goals
- Full password / OAuth2 login flows (use instant multi-tab profile switching + shareable friend/group invite codes for immediate multiplayer use across browser tabs, phones, and glasses).
- Modifying Meta Wearables DAT Android SDK internals or violating `glasses-app/` `<ButtonRail>` / UI Toolkit rules.
- Full Google Earth / Photorealistic 3D Tiles rendering right now (deferred — we will store optional `lat`/`lon` coordinates on outdoor place pins and spaces, matching `ImuSample.lat`/`lon`, so Google Earth outdoor view plugs in cleanly later).

### Anti-Goals
- Leaving legacy construction/dev data (`Red DeWalt Drill`, `LIVE 400V PANEL`, `Breaker Panel`, `drywall boxes`, `Alex/Sam`) visible in everyday product views.
- Generating false `"X moved Item from Zone A to Zone A"` events merely because another family member walked past an unmoved item.
- Leaking `private`, `people`, or non-member `group` pins/spaces over `/api/state` or `/ws/stream` when `viewer_id` is supplied.
- Blocking live UI or interrupting one viewer's screen when another viewer switches spaces.

---

## 3. Approaches Considered

| Approach | Summary | Trade-offs | Decision |
|---|---|---|---|
| **A. Consumer Frontend in `worldgit/frontend/` + Social/History/Multi-Space Backend (`worldgit/backend/`)** | Upgrade `frontend/` into the everyday web/mobile UI with `"Map & Scan Studio"` inside a secondary drawer; extend `backend/main.py` & `db.py` additively with real social/history models and per-viewer `world_id` support. | Single Vite app, reuses working Three.js point cloud viewer, `video_localizer.py`, `:8000` backend, `glasses-app/`, and Android app. Zero duplicated 3D renderers. | **Picked** |
| **B. Separate Consumer Web App (`worldgit/consumer-app/`)** | Keep `frontend/` purely for dev testing and build a second standalone consumer frontend. | Duplicates Three.js rendering, WS sync, proxy/tunnel routing, and maintenance. | Rejected |
| **C. Glasses & Voice-First Only** | Keep almost all interactions headless inside `glasses-app/` & Android companion. | Lacks ergonomic browsing of 3D spaces, friend reviews feed, item movement history, and group permission controls. | Rejected |

---

## 4. Product Data Model & Resolved Engineering Calls (`worldgit/backend/`)

### 4.1 Real Spaces & Per-Client World Routing *(Call #1 & #3 resolved)*
- **Only Real Mapped Spaces + Live Async Scan Queue:**
  - Startup exposes the real reconstructed space(s) registered in `MultiWorldManager` (`world_1`, labeled `"Room Scan"` or user-renamed `"Bedroom 402"` / `"Home"`, etc.), plus a **`+ Scan / Add Space`** flow backed by `/api/dat-scans` and `/api/reconstruct-video-bytes` with an in-memory/DB `scan_jobs` status tracker (`pending` | `processing` | `ready` | `failed`) so glasses live scans or `.mp4` uploads process asynchronously in the background and appear in the Space selector automatically when done.
  - **Unmapped Place Reviews (e.g. `Subway` storefront review before 3D scan):** Stored cleanly as `PlaceReview` / scoped `SpatialPin` (including optional `lat`/`lon` GPS fields ready for a future Google Earth / 3D Tiles outdoor layer — attached to a real 3D space when mapped inside one, or surfaced under the **Places & Reviews** view with storefront preview photo + author + audience scope + option to attach/scan a 3D space for it later). Zero fake geometry invented.
- **Per-Client `world_id` Routing (Not Global Overwrite):**
  - Cache loaded world JSON payloads (`points`, `keyframes`, `wall_segments`, `room_envelope`) by `world_id` in `backend/main.py` instead of overwriting one global array.
  - `GET /api/state?world_id=...&viewer_id=...` and `GET /api/points.bin?world_id=...` serve the requested space independently per browser tab.
  - `WS /ws/stream?viewer_id=...&world_id=...` tracks `(viewer_id, world_id)` per connected socket so tab 1 (`Conor` viewing Space A) and tab 2 (`Mum` viewing Space B, or both viewing Space A together) never clobber each other.
  - Legacy endpoints without `world_id` default seamlessly to `idx["active_world_id"]`.
- **Space Visibility (`spaces_meta` table):**
  - Each `world_id` has `{owner_id, audience_type ("public"|"friends"|"group"|"private"), group_ids, display_name}`. Newly uploaded scans default to `owner_id`'s `Family` group (`audience_type="group"`, `group_ids=["grp-family"]`) or creator's chosen scope; user can update space sharing anytime.

### 4.2 Identity, Friends, Groups & Strict Visibility Filtering *(Call #2 & #7 resolved)*
- **Database Tables (`backend/db.py`, both PostgreSQL & SQLite):**
  - `users`: `{user_id, name, handle, avatar_color, friend_code}` (defaults include `usr-conor`, `usr-mum`, `usr-dad`, `usr-alex`, `usr-sarah`, plus dynamic profile creation).
  - `friendships`: symmetric `(user_a, user_b, created_at)`.
  - `groups` & `group_members`: `{group_id, name, kind ("family"|"friends"|"custom"), owner_id, invite_code}`.
- **`wearer_id` ↔ `user_id` Mapping:**
  - Maintain explicit mapping: `"wearer-b"` ↔ `"usr-conor"`, `"wearer-a"` ↔ `"usr-alex"`, `"wearer-c"` ↔ `"usr-mum"`, and auto-create/resolve `wearer-<slug>` for any newly created user profile (`WearerState.user_id` optional field added additively). Existing Android / `glasses-app/` hardware telemetry for `"wearer-b"` drives Conor's avatar directly without duplicate avatars.
- **Unified Visibility Rule (`can_user_view(record, viewer_id, db)`):**
  - Shared helper used identically for **Spaces**, **Scoped Pins / Place Reviews**, **Tracked Objects**, and **Movement Timeline Events**:
    - `author_id / owner_id == viewer_id` → **allowed**
    - `audience_type == "public"` → **allowed**
    - `audience_type == "private"` → author only
    - `audience_type == "friends"` → symmetric friend of author
    - `audience_type == "group"` → viewer belongs to at least one group in `group_ids`
    - `audience_type == "people"` → `viewer_id in target_user_ids`
  - **When `viewer_id is None` (legacy `glasses-app/` / scripts omitting `viewer_id`):** defaults to `viewer_id="usr-conor"` for compatibility with Conor's glasses (`wearer-b`), OR `public + grp-family`? **Call made:** default omitted `viewer_id` on `:8000` glasses/legacy endpoints to `"usr-conor"` (while explicit `viewer_id="guest"` sees **only** `"public"`). This prevents Conor's glasses HUD from losing access to his own & family items, while testing multi-user privacy strictly when any profile or `"guest"` is selected.

### 4.3 Named Zones & Object Movement Ledger (`seen` vs `moved`) *(Call #5 resolved)*
- **Named Zones (`spatial_zones` table):**
  - `{zone_id, world_id, name, x, y, z, radius_m, created_by}`.
  - Users can click any 3D point on a mapped space to create/name a zone (e.g., `"Kitchen Table"`, `"Sitting Room Press"`, `"Beside Left Bed"`).
  - Helper `resolve_zone(world_id, position, fallback_hint)` picks the nearest zone containing `position` (`dist <= radius_m`), else uses cleaned `fallback_hint`, else `"Room"`.
- **Object Movement Ledger (`object_events` table):**
  - Stores `{event_id, object_id, label, world_id, event_type ("placed"|"moved"|"seen"), actor_user_id, actor_name, from_zone, from_pos, to_zone, to_pos, summary, timestamp}`.
  - **Clean State-Transition Rule (`POST /api/objects/sighting`):**
    1. First time `object_id` (or normalized item label in that scope) is seen → `event_type = "placed"`, summary = `"{actor_name} left {label} at {to_zone}"`.
    2. Already tracked, and either `to_zone != prev_zone` **or** `distance(pos, prev_pos) >= 0.35m`:
       - `event_type = "moved"`
       - Updates `spatial_objects` position/zone/`seen_by`/`timestamp`
       - Appends transition with `from_zone` / `from_pos` → `to_zone` / `to_pos`, summary = `"{actor_name} moved {label} from {from_zone} to {to_zone}"`!
    3. Already tracked, same zone **and** displacement `< 0.35m`:
       - Updates `last_confirmed_at` / optional `event_type = "seen"` (throttled, not surfaced as a fake `"moved"` event in the user's movement feed!).
- **Cross-Space Natural QA (`POST /api/ask` & enhanced `POST /api/ctrl-f`):**
  - Searches **all spaces visible to `viewer_id`** (preferring active `world_id` on tie), returns `{found, world_id, space_name, object, latest_move, history, path, distance_m, bearing_deg, arrow, spoken_answer}`.
  - Example natural answer: `"You left your Charger at Kitchen Table, and Mum moved it to Press in Sitting Room (3.4m ahead-left)."` or `"Your Water Bottle is beside the left bed in Room Scan (2.9m ahead-left)."` — also playable via `/api/tts`.
  - If an item is found in another mapped space than the viewer's current tab, the frontend automatically offers/jumps to that space (`world_id`) and frames the item + **From → To movement arc**.

### 4.4 Startup Migration & Pre-Seeded State *(Call #4 resolved)*
- Replace legacy industrial seed items (`obj-drill`, `obj-socket`, `obj-medkit`, `obj-battery`, `obj-laser`, industrial `pin-1`/`pin-2`, old `Alex/Sam` jobsite wearers) with a one-time idempotent DB migration on `SpatialDatabase` init that purges known industrial demo IDs if present in `worldgit_aiven_cache.db` / Postgres while **never wiping real user-created notes, objects, zones, or scans**.
- Seed clean everyday user profiles (`Conor`, `Mum`, `Dad`, `Alex`, `Sarah`), groups (`Family`, `Otaniemi Foodies`), friendships, and if `world_1` (`Room Scan`) has no objects yet, restore/keep the localized `Water Bottle` (`[-0.719, 0.055, 0.697]` beside left bed) + `Beside Left Bed` zone without calling `world.objects.clear()` after seeding!

### 4.5 Bridge from `:8765` (`yolo_world`) to `:8000` *(Call #6 resolved)*
- **`PlaceAnchorStore` reviews (`POST /api/places`):** Optionally accepts `{author_id, world_id, position, audience_type, group_ids, target_user_ids}` and forwards a normalized `POST /api/pin` record (`pin_type="friend_memory"`, `place_name`, `rating`, default `audience_type="friends"`, default `world_id=None` for neighborhood/place feed or active space + fallback `[0, 1.2, 0]` if attached to a mapped space).
- **`SpatialNoteStore` (`POST /api/notes`):** Forwards `author_id`, `audience_type` (default `"friends"`), `group_ids`, `target_user_ids` alongside 3D raycast position to `:8000`.
- **`ObjectMemoryStore` (`POST /api/objects/sighting` hook):** Whenever a personal item detection settles in an active mapped space, optionally posts non-blocking sighting updates (`{world_id, label, position, zone_hint: spatial_phrase, actor_user_id}`) using the wearer's current pose + estimated depth so live glasses feeds update shared family memory automatically.

---

## 5. Consumer UI Layout (`worldgit/frontend/`)

Refactor `worldgit/frontend/src/App.jsx` into clean modular components (`src/components/TopBar.jsx`, `SideDrawer.jsx`, `SpacesPanel.jsx`, `NotesComposerModal.jsx`, `StudioDrawer.jsx`):

1. **Top Consumer Navigation Bar:**
   - **Spaces Selector:** Switch between mapped spaces + see background scan jobs (`Processing 42%...`) + `+ Scan / Upload Space`.
   - **"Ask Your Spaces" Bar:** Type or speak queries (*"Where's my charger?"*, *"Have I left anything behind?"*) → shows spoken/text answer pill + speaker button (`/api/tts`) + switches space & flies 3D camera directly to the item and its **From → To movement arc**.
   - **Active User Switcher (`?user=` / `sessionStorage`):** Switch between `Conor`, `Mum`, `Dad`, `Alex`, `Sarah`, `Guest (Public Only)`, or `+ New Profile` per browser tab so two open tabs test live multiplayer presence, permission filtering, and object handoffs instantly.

2. **Interactive 3D Viewport:**
   - Clean 3D point cloud of the selected space (`world_1` or any newly scanned space).
   - **Live Wearer Avatars:** Moving beacons & name tags for online users in the same space (plus click-to-move/simulate wearer position for multi-tab demos without physical glasses).
   - **Audience-Badged 3D Pins:** `Public`, `Friends`, `Family / Group`, `Direct`, `Only Me` color badges. Clicking a pin opens its detail card.
   - **Item Beacons & Movement Arcs:** Selected/queried items show glowing ground beacon + dashed **From Zone → To Zone** 3D arc with timestamps & who moved it.
   - **3D Click Action:** Clicking any 3D point opens a compact inline popover: **"Drop Note Here"** (with audience selector) or **"Name This Zone"** (`"Kitchen Table"`, `"Sitting Room Press"`, etc.).

3. **Collapsible Side Panel (3 Consumer Tabs):**
   - **Things & History:** Tracked items in visible spaces, `"Check Left Behind"`, chronological movement ledger (`"Mum moved Charger from Kitchen Table → Sitting Room Press"`), and 1-click camera focus.
   - **Notes & Places:** Visible spatial notes & friend restaurant reviews (`Subway`, ratings, photo thumbnail if from `:8765`, audience badge) + **`+ Add Note / Review`** with audience picker (`Public` / `Friends` / `Group(s)` / `Specific People` / `Only Me`).
   - **People & Groups:** Live active users in space, friend list + `"Add Friend by Code/Handle"`, group list + `"Create / Join Group"` with shareable invite code.

4. **"Map & Scan Studio" Secondary Drawer:**
   - Collapsible drawer (closed by default) keeping all reconstruction & calibration utilities intact: `Locate Video on 3D Map`, `Focus Bottle`, `Load VGGT Walk-Around`, `Upload .MP4 (VGGT)`, `Point Cloud / AI Surface Fill / Architectural Shell / Point Size`, `Camera Path`, `Auto-Turntable`, and `Clear / Reset 3D Map`.

---

## 6. Left to the Builder

- Exact component props, CSS styling details, and responsive layout breakpoints matching the existing dark translucent card aesthetic (`#050811`, `rgba(15, 23, 42, 0.85)`).
- Exact Playwright, `unittest`, and Vitest/build verification tasks in the implementation plan.
- Exact `scan_jobs` in-memory/SQLite progress bookkeeping during async `/api/dat-scans` and `/api/reconstruct-video-bytes` execution.
