# WorldGit — Explainer & Architecture Guide

> **Canonical Reference:** See [`docs/META_SUBMISSION.md`](docs/META_SUBMISSION.md) for the authoritative specification of live hardware behavior, privacy boundaries, provenance labels, and current limitations. The sections below summarize the broader Studio & Web simulator architecture (including pre-reconstructed rooms and seeded examples):
> - **Live observations vs. pre-reconstructed rooms:** Unlocalized 2D glasses detections live in `:8765` `ObjectMemoryStore` with last-seen age and neighbor context; `[x, y, z]` spatial paths and raycasted HUD pins apply only when the target exists in an active pre-reconstructed or explicitly scanned 3D room.
> - **Restore instructions (`Ctrl+Z`):** `Ctrl+Z` computes and displays step-by-step **restore instructions** between snapshots; it does not physically move or revert objects.
> - **Demo identity & seeded data:** Studio demo wearers/worksite fixtures (`Alex`, `Conor`, `Red DeWalt Drill`, etc.) are seeded examples, and `viewer_id` is a local demo selector rather than authenticated multi-user security.

---

## 1. What WorldGit Is

Software engineers take `Ctrl+F`, `Ctrl+Z`, and `git diff` for granted. In the physical world—on construction sites, workshops, labs, shared homes, or exploring a new city—there is no shared memory, no search bar, and no undo button.

**WorldGit** turns every pair of **Meta Ray-Ban Display** glasses into a node in a **shared spatial memory network**:

1. **Explicit Room Mapping (`POST /api/dat-scans` / Pre-Reconstructed Rooms)**  
   Pre-reconstructed room point clouds (`world_1`) or explicit uploaded video + IMU scans build the Studio 3D map (`backend/` + optional GPU reconstruction). Live glasses frame detections that are not localized into a 3D map stay in live 2D object memory (`:8765`).
2. **`Ctrl+F` & Live Memory Search**  
   Ask your glasses: *"Where did I leave my bottle?"* or *"Where is the Red DeWalt Drill?"* Live object memory returns last-seen context and age (`"Last seen 4 minutes ago beside the laptop"`); when an object is localized in an active 3D map, Studio also provides approximate `[x, y, z]` guidance and turn cues.
3. **Spatial Voice Triggers & *Friend's World***  
   - **Room / Team Notes:** Anchor live notes onto detected objects (`SpatialNoteStore` + approximate Studio raycast pins when a 3D map is active) or view seeded demo reviews.
   - **Friend's World (Seeded & Local Demo Layer):** Seeded demo memories and place anchors (e.g., demo cafe/restaurant pins via `seed_demo_places.py`).
4. **Reality `git diff` & `Ctrl+Z` Restore Instructions**  
   Compare two spatial snapshots (`pass-1-initial` vs `pass-2-current`) to inspect `MOVED`, `MISSING`, or `ADDED` entries. Trigger **`Ctrl+Z`** to generate step-by-step **restore instructions** (guide only — e.g., *"Move Red DeWalt Drill 2.4m back to Workbench A"*).
5. **Live Co-Op Crew Minimap**  
   Track every wearer's live `[x, y, z]` position, yaw, and gaze cone in real time across the 3D Command Center and HUD.

---

## 2. All 6 Tracks & Partner Challenges Targeted

| Track / Challenge | How WorldGit Uses It | Key Files | Offline / Zero-Key Fallback |
|---|---|---|---|
| **Meta — AI Glasses for Everyone, Everyday** *(Main Track)* | Official `@wearables-ui-toolkit/mrbd` 600×600 transparent HUD, Neural Band / D-pad navigation, and `document.modelContext` WebMCP voice tools | `glasses-app/src/` | Full standalone UI + WebMCP tools |
| **Google DeepMind — Best Use of Gemini API** | `gemini-2.5-flash` multimodal POV frame object detection (`analyze_frame_with_gemini`) and `Ctrl+Z` physical restore reasoning (`generate_ctrl_z_instructions`) | `backend/ai_services.py` | Deterministic mock detections & restore steps |
| **Modal — Best Use of Modal** | Serverless `L4` GPU endpoint running `depth-anything/Depth-Anything-V2-Small-hf` to backproject RGB frames + wearer pose into colored 3D world points | `gpu_worker/reconstruct.py` | Procedural 1,246-point workshop room cloud in `backend/main.py` |
| **ElevenLabs — Best Use of ElevenLabs** | Low-latency spatial voice whispers (`eleven_turbo_v2_5`) served via `POST /api/tts` | `backend/ai_services.py` | Browser `window.speechSynthesis` fallback |
| **Aiven — Best Use of Aiven** | Managed PostgreSQL (`AIVEN_DATABASE_URL`) persisting `spatial_objects`, `spatial_pins`, and `world_snapshots` | `backend/db.py` | Automatic local SQLite cache (`worldgit_aiven_cache.db`) |
| **OpenAI — Best Use of Codex** | Multi-agent engineering workflow (`CODEX_WORKFLOW.md`) + `gpt-4o-mini` reasoning fallback in `ai_services.py` | `CODEX_WORKFLOW.md`, `backend/ai_services.py` | Deterministic fallback |

---

## 3. System Architecture & Local Ports

```text
+-----------------------------------+       WebSocket /ws/stream + REST       +------------------------------------+
|  Meta Ray-Ban Display HUD         | <-------------------------------------> |  WorldGit Spatial Hub (FastAPI)    |
|  glasses-app/ (Port 4173)         |                                         |  backend/ (Port 8000)              |
|  - @wearables-ui-toolkit/mrbd     |                                         |  - main.py (state, diff, ctrl-f/z) |
|  - 600x600 transparent HUD        |                                         |  - ai_services.py (Gemini/OpenAI/  |
|  - WebMCP (document.modelContext) |                                         |    ElevenLabs TTS)                 |
+-----------------------------------+                                         |  - db.py (Aiven PG / SQLite cache) |
                                                                              +--------+------------------+--------+
+-----------------------------------+       WebSocket /ws/stream + REST                |                  |
|  Desktop 3D Command Center        | <------------------------------------------------+                  |
|  frontend/ (Port 5173)            |                                                                     v
|  - React + Three.js WebGL         |                                         +------------------------------------+
|  - Point cloud, gaze cones,       |                                         |  Modal Cloud GPU Worker            |
|    Ctrl+F path, git diff vectors, |                                         |  gpu_worker/reconstruct.py         |
|    snapshot time-travel scrubber  |                                         |  - L4 GPU Depth-Anything-V2        |
+-----------------------------------+                                         +------------------------------------+
```

- **Meta Display HUD:** `http://127.0.0.1:4173/` (`glasses-app/`)
- **3D Command Center:** `http://127.0.0.1:5173/` (`frontend/`)
- **Spatial Hub API + WS:** `http://127.0.0.1:8000/` (`backend/`)
- **Meta Validator Toolkit:** `meta-wearables-webapp-ref/` (includes `validate-ui-toolkit.mjs` & `check-webapp.mjs`)

---

## 4. Parallel 4-Lane Team Workflow (Zero Merge Conflicts)

Four teammates (and their AI coding assistants) can build simultaneously by staying strictly inside their assigned directory lane:

| Lane | Owner Directory | Responsibilities | Do NOT Edit |
|---|---|---|---|
| **Lane 1: Glasses HUD** | `glasses-app/` | `@wearables-ui-toolkit/mrbd` HUD views, Neural Band / D-pad flow, WebMCP tools (`webmcp.ts`) | `frontend/`, `backend/`, `gpu_worker/` |
| **Lane 2: 3D Command Center** | `frontend/` | Three.js point cloud, wearer gaze cones, `Ctrl+F` path overlay, `git diff` vectors, snapshot scrubber, webcam POV capture | `glasses-app/`, `backend/`, `gpu_worker/` |
| **Lane 3: Spatial Hub Backend** | `backend/` | FastAPI endpoints, WebSocket broadcast, Gemini/OpenAI prompts, ElevenLabs TTS, Aiven PostgreSQL schema | `glasses-app/`, `frontend/`, `gpu_worker/` |
| **Lane 4: Cloud 3D GPU** | `gpu_worker/` | Modal `L4` GPU container, `Depth-Anything-V2` inference, camera intrinsics & 3D unprojection | `glasses-app/`, `frontend/`, `backend/` |

> **Golden Rule:** Always run `git pull --rebase origin main` before starting work and before pushing. Never modify another lane's files without coordinating.

---

## 5. Frozen API & WebSocket Contracts

All 4 lanes depend on these stable payloads. You may add **optional** fields, but **never rename or remove existing keys**:

### `GET /api/state`
Returns the full spatial world state:
```json
{
  "wearers": [{ "id": "alex", "name": "Alex", "position": [x, y, z], "yaw": 0.4, "color": "#38bdf8" }],
  "objects": [{ "id": "drill", "name": "Red DeWalt Drill", "position": [x, y, z], "seen_by": "Alex", "last_seen": "2m ago", "confidence": 0.96 }],
  "pins": [{ "id": "pin-1", "type": "warning|memory|note", "author": "Sam", "age_label": "2 years ago", "text": "...", "position": [x, y, z], "trigger_radius_m": 1.8 }],
  "snapshots": [{ "id": "pass-1-initial", "label": "Pass 1 (Initial)", "timestamp": "..." }],
  "point_cloud": [{ "x": 0.0, "y": 0.0, "z": 0.0, "r": 200, "g": 200, "b": 200 }],
  "active_ctrl_f": null,
  "diff": { "moved": [], "missing": [], "added": [] },
  "integrations": { "gemini": false, "openai": false, "elevenlabs": false, "aiven": false, "modal": false }
}
```

### `POST /api/ctrl-f`
Request: `{ "query": "drill", "wearer_id": "conor" }`  
Response: `{ "found": true, "object": { ... }, "distance_m": 3.2, "direction_text": "...", "path": [[x,y,z], ...], "spoken_text": "..." }`

### `GET /api/ctrl-z`
Response: `{ "from_snapshot": "pass-1-initial", "to_snapshot": "pass-2-current", "diff": { "moved": [...], "missing": [...], "added": [...] }, "steps": ["..."], "spoken_summary": "..." }`

### `POST /api/pin`
Request: `{ "author": "Conor", "type": "warning|memory|note", "text": "...", "position": [x, y, z], "trigger_radius_m": 1.8, "age_label": "just now" }`

### `POST /api/analyze-frame`
Request: `{ "image_base64": "...", "wearer_id": "conor", "position": [x, y, z], "yaw": 0.0 }`  
Response: `{ "detected_objects": [...], "points_added": 120, "provider": "gemini|openai|fallback" }`

### `POST /api/tts`
Request: `{ "text": "..." }`  
Response: `audio/mpeg` bytes when `ELEVENLABS_API_KEY` is set, or `{ "fallback": "browser_speech_synthesis", "text": "..." }`.

### `WS /ws/stream`
Broadcasts `{ "type": "state_update", "state": <GET /api/state payload> }` whenever objects, pins, wearer poses, or `Ctrl+F` targets change.

---

## 6. Quick Start

```powershell
# Terminal 1: Backend Spatial Hub (http://127.0.0.1:8000)
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # optional — works 100% offline with empty .env
uvicorn main:app --host 127.0.0.1 --port 8000

# Terminal 2: Meta Ray-Ban Display HUD (http://127.0.0.1:4173)
cd glasses-app
npm install
npm run build
npm run preview -- --host 127.0.0.1 --port 4173

# Terminal 3: Desktop 3D Command Center (http://127.0.0.1:5173)
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

---

## 7. 90-Second Live Judge Demo Script

1. **0:00–0:20 (Multiplayer Fog-of-War):** Open the 3D Command Center (`5173`) beside the Meta Ray-Ban Display Simulator (`4173`). Show 3 teammates' live gaze cones and the shared 3D point cloud.
2. **0:20–0:40 (`Ctrl+F` Physical Search):** On the Glasses HUD, trigger `Ctrl+F` for **"Red DeWalt Drill"** (last seen by Alex). Watch the dashed 3D path light up in WebGL while the HUD shows distance + spoken ElevenLabs guidance.
3. **0:40–1:05 (*Friend's World* & Spatial Pins):** Trigger a proximity pin — both a worksite hazard warning (*"Live 240V conduit"*) and a *Friend's World* memory (*"Sam • 2 years ago: My favourite flat white in Helsinki"*).
4. **1:05–1:30 (Reality `git diff` & `Ctrl+Z`):** Toggle the `git diff` view (`pass-1-initial` → `pass-2-current`), highlight the moved drill and missing wrench, and press **`Ctrl+Z`** to hear step-by-step physical restoration instructions.
