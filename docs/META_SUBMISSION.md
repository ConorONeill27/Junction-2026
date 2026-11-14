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
  - Explicit **Memory: ON / PAUSED** toggle (`POST /api/privacy`) that immediately stops passive memory capture and displays `PRIVACY: MEMORY PAUSED` on the glasses HUD.
  - One-tap **Clear** control (`DELETE /api/memory`) to wipe stored snapshots and metadata.
  - Local-first storage on the user's own server (`notes/`, `places/`, `memory/`).
- **Device & Network Resilience:**
  - Graceful handling of camera/microphone permissions, glasses connect/disconnect state changes, server disconnects, and `adb reverse tcp:8765 tcp:8765` / LAN fallback.

---

## 5. Plausible Path to Adoption / Pilot

1. **Phase 1 — Personal & Household Pilot:** Opt-in everyday memory for keys, medication, water bottles, and shared kitchen/office notes.
2. **Phase 2 — Assisted Living & Accessibility Pilot:** Partner with occupational therapists and accessibility groups to evaluate spoken object-finder cues and room-handoff reminders for older adults and users with low vision or memory impairments.
3. **Phase 3 — Local Neighborhood Place Layers:** Opt-in friend-circle storefront notes and reviews without requiring full 3D city mapping.

---

## 6. Architecture & Verification Summary

- **Android Glasses App (`backend/yolo_world/android-display-access/`):** Meta DAT SDK integration, calibrated HUD overlay (`YoloOverlay.kt`), Live Transcription (`LiveTranscriptionViewModel.kt`), TTS + Memory + Privacy controls (`YoloViewModel.kt`, `YoloScreen.kt`, `DisplayViewModel.kt`, `AppScaffold.kt`).
- **Real-Time Vision Server (`:8765`, `backend/yolo_world/`):** YOLO-World detection, `SpatialNoteStore`, `PlaceAnchorStore`, `ObjectMemoryStore`, and FastAPI/WebSocket server (`detection_server.py`, `run.py`).
- **3D Studio & Reconstruction Backend (`:8000`, `backend/main.py` & `frontend/`):** VGGT-1B 3D room reconstruction, RANSAC floor leveling + corridor carving, `/api/pin-from-hud` raycasting, and Three.js 3D Studio.
- **Automated Checks:**
  - Python unit tests: `python -m unittest discover -s backend/yolo_world -p "test_*.py" -v`
  - Frontend builds: `npm run build` in `frontend/` and `glasses-app/`
  - Android unit tests & APK build: `gradlew testDebugUnitTest assembleDebug`
