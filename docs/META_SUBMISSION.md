# WorldGit — Meta "AI Glasses for Everyone, Everyday" Submission

**Tagline:** *Ctrl+F for physical reality — hands-free spatial notes, visual place reviews, and object memory on Meta AI glasses.*

---

## 1. Problem & Intended Audience

Everyday spaces — homes, shared workspaces, kitchens, labs, classrooms, and city streets — lack persistent, hands-free digital context. People constantly ask:
- *"Where did I leave my water bottle or keys?"*
- *"What did my teammate or partner leave on this desk/counter?"*
- *"Is this restaurant any good according to people I trust?"*

Pulling out a phone breaks attention, requires two hands, and excludes users with mobility, visual, or situational impairments. **WorldGit** turns Meta AI glasses into a first-person, hands-free spatial memory layer for:
- **Shared households, roommates, and workplace teams** leaving contextual notes in physical rooms.
- **Everyday pedestrians & travelers** seeing trusted friend reviews pinned directly onto storefronts.
- **People with ADHD, memory challenges, or visual/motor impairments** who benefit from automatic object-last-seen memory and spoken TTS audio readouts without touching a phone.

---

## 2. Why First-Person AI Glasses (Beyond Phone Mirroring)

A phone screen requires stopping, unlocking, aiming, and holding the device. Meta AI glasses provide:
1. **Continuous first-person field of view:** Object detection, storefront matching, and spatial note re-ID run passively from the wearer's natural line of sight.
2. **Hands-free, in-the-moment HUD + Audio:** High-contrast HUD cards appear over the relevant object or storefront while optional Android `TextToSpeech` reads notes, reviews, and finder results aloud (with a 6-second per-item cooldown so audio stays helpful, never overwhelming).
3. **Zero-friction capture:** Voice transcription lets a user pin a note onto whatever they are looking at without typing.

---

## 3. Three End-to-End Everyday Demos

### Demo 1 — Room Note Handoff + 3D Studio (`:8765` ↔ `:8000`)
- **Person A** looks at an object/spot in the room and speaks a note.
- The `:8765` server (`backend/yolo_world/spatial_notes.py`) saves a persistent spatial note anchor (text, object label, HSV crop signature, full frame, timestamp) and forwards it to `:8000` (`POST /api/pin-from-hud`), where depth raycasting (`_raycast_world_point` in `backend/main.py`) resolves `[x, y, z]` inside the reconstructed 3D room (`world_1`, 281,772 cleaned points).
- **Person B** walks into the room from a different angle: `SpatialNoteStore` matches the visual crop + 3D-to-2D projection and re-pins Person A's note directly onto Person B's glasses HUD.

### Demo 2 — Visual Place Reviews While Walking Past (No 3D Scan Required)
- `backend/yolo_world/place_anchors.py` (`PlaceAnchorStore`) matches live glasses frames against reference storefront images using ORB feature matching + RANSAC homography.
- Pre-seeded with demo restaurants via `seed_demo_places.py` (including Burger King `1/5`, Momotoko Ramen `4/5`, Espresso House `5/5`, and Subway `3/5`) so walking past displays friend name, star rating, and review snippet directly on the HUD.

### Demo 3 — Water Bottle Memory Across Rooms (No 3D Scan Required)
- `backend/yolo_world/object_memory.py` (`ObjectMemoryStore`) passively records target everyday items (`bottle`, `keys`, `phone`, etc.) with an annotated snapshot, object crop, timestamp, and co-occurring neighbor context (e.g., `"next to laptop"`).
- Tapping **Find bottle** (or querying `/api/memory/find`) surfaces a visual memory card on the phone + a high-contrast banner on the glasses HUD and speaks the last-seen location via TTS.

---

## 4. Accessibility, Privacy & Consent, and Resilience

- **Accessibility:**
  - High-contrast HUD cards and calibrated display bounds (`hud_calibration.json` + phone `SharedPreferences`).
  - Toggleable **Voice: ON/OFF** (`TextToSpeech` readout for pinned notes, place reviews, and bottle-finder results).
- **Privacy & Consent:**
  - **Strict Privacy Pause (`POST /api/privacy`):** Stops frame uploads on the Android app (`receiveGlassesVideoFrame` returns immediately), clears live HUD previews/boxes, drops `latest_frame` and `latest_detections` on `:8765`, rejects/short-circuits `/api/frame` processing while paused, and displays `WorldGit • Privacy Paused` with a one-tap **Resume** button on the glasses HUD.
  - **Full Session Wipe (`DELETE /api/session`):** One-tap **Clear** wipes object memory snapshots (`memory/`), saved spatial notes (`notes/`), place anchors (`places/`), recorded audio clips (`audio_clips/`), and in-memory frames/detections.
  - **Localhost-by-Default & Optional Auth:** The `:8765` Vision server binds to `127.0.0.1` by default (routed over USB `adb reverse tcp:8765 tcp:8765`) and supports `WORLDGIT_API_TOKEN` / `X-WorldGit-Token` header verification.
- **Accessible & Inclusive Input:**
  - **Head-Aim-Free `Next` Cycling:** In glasses `Pins` mode, wearers can either aim with head motion or tap **Next** in the Bloks `buttonGroup` to cycle through detected objects without fine neck/head aiming.
  - **Multi-Instance Object Disambiguation:** `ObjectMemoryStore` compares 8×8 Hue-Saturation crop histograms to distinguish multiple items of the same class (e.g. `bottle` vs `bottle #2`) and reports age/staleness (`just now`, `5m ago`).
- **Device & Network Resilience:**
  - Graceful handling of camera/microphone permissions, glasses connect/disconnect state changes, automatic clearing of stale HUD boxes if `:8765` drops for >2.5s, and `adb reverse` USB tunneling.

---

## 5. Proposed Next Steps: Path to Adoption / Pilot (Not Yet Field-Tested)

> *Note: Within the hackathon scope, no external user study or clinical trial has been conducted yet. The following is the proposed pilot roadmap:*

1. **Proposed Phase 1 — Household Opt-In Trial:** Evaluate everyday object-memory recall (`bottle`, `keys`, `medication`) and room-note handoffs across 3–5 shared households, measuring retrieval latency and false-positive rate.
2. **Proposed Phase 2 — Accessibility Co-Design:** Partner with occupational therapists and low-vision / ADHD user groups to refine spoken TTS cadence, `Next` button navigation, and HUD contrast.
3. **Proposed Phase 3 — Opt-In Neighborhood Place Layers:** Extend local ORB place anchors to shared friend-circle notes with explicit per-place consent.

---

## 6. Architecture & Verification Summary

- **Native Android Glasses App (`worldgit-hud-android/`):** Meta DAT SDK integration (`WearablesSessionManager.kt`), 60 Hz IMU fusion + Bloks HUD (`WorldGitViewModel.kt`, `WorldGitHudRenderer.kt`), local optical tracking, voice transcription, TTS, and Privacy Pause / Wipe controls.
- **Real-Time Vision Server (`:8765`, `backend/yolo_world/`):** YOLO-World detection, `SpatialNoteStore`, `PlaceAnchorStore`, `ObjectMemoryStore` (HSV multi-instance + staleness), and `detection_server.py`.
- **3D Studio & Spatial Hub (`:8000`, `backend/main.py` & `frontend/`):** Pre-scanned 3D room viewer, spatial diffing, Gemini Q&A, and `/api/pin-from-hud` approximate projection bridge.
- **Automated Checks:**
  - Python unit tests: `backend\.venv311\Scripts\python.exe -m unittest discover -s backend/yolo_world -p "test_*.py" -v`
  - Android APK build: `.\gradlew.bat assembleDebug` in `worldgit-hud-android/`
  - Frontend build: `npm run build` in `frontend/` and `glasses-app/`
