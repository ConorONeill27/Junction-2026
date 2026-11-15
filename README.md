# WorldGit — Ctrl+F & Spatial Memory for Meta AI Glasses

**Hands-free object memory, spoken spatial notes, and 3D room context for Meta Ray-Ban Display AI glasses.**

> **Docs & Submission:**
> - [`docs/META_SUBMISSION.md`](docs/META_SUBMISSION.md) — Meta challenge submission, privacy/accessibility controls, and honest scope
> - [`EXPLAINER.md`](EXPLAINER.md) — Full system architecture, API contracts, and demo script
> - [`worldgit-hud-android/`](worldgit-hud-android/) — **Native Android + Meta DAT SDK glasses app** (live glasses camera, 60 Hz IMU fusion, Bloks HUD, voice transcription, and TTS)

## Architecture & Components

| Component | Purpose | Location |
|---|---|---|
| **Native Meta Display App** | Real Meta DAT SDK integration: glasses camera stream, Bloks HUD (`Ask` compass, `Pins` overlay, `Next` object cycling, `Undo`), Whisper/Gemini voice notes, TTS, and Privacy Pause | [`worldgit-hud-android/`](worldgit-hud-android/) |
| **Real-Time Vision Server (`:8765`)** | YOLO-World object detection, heuristic HSV multi-instance object memory (`ObjectMemoryStore`), spatial notes (`SpatialNoteStore`), ORB place anchors, Privacy Pause (with in-flight inference discard), and cross-service `DELETE /api/session` wipe | [`backend/yolo_world/`](backend/yolo_world/) |
| **3D Studio & Spatial Hub (`:8000`)** | Room reconstruction (`VGGT-1B` / `Depth-Anything-V2`), spatial diffing, Gemini Q&A, and ElevenLabs/browser TTS | [`backend/`](backend/) |
| **3D Studio Web UI (`:5173`)** | Desktop Three.js spatial viewer, object-pin inspector, and live glasses telemetry | [`frontend/`](frontend/) |
| **Web HUD Simulator (`:4173`)** | Browser/MRBD-simulator companion HUD for desktop testing without hardware | [`glasses-app/`](glasses-app/) |

## Quick Start

### 1. Real-Time Vision Server (`127.0.0.1:8765`, USB `adb reverse`)
```powershell
cd backend\yolo_world
..\.venv311\Scripts\python.exe run.py --source phone --headless
```
> Binds to `127.0.0.1` by default (use `adb reverse tcp:8765 tcp:8765` for USB-connected Android phones; set `WORLDGIT_API_TOKEN` to require `X-WorldGit-Token` on API requests).

### 2. Native Android + Meta Display App (`worldgit-hud-android`)
```powershell
cd worldgit-hud-android
.\gradlew.bat assembleDebug
adb reverse tcp:8765 tcp:8765
adb reverse tcp:8000 tcp:8000
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

### 3. Spatial Hub (`127.0.0.1:8000`) & 3D Studio (`127.0.0.1:5173`)
```powershell
cd backend
.\.venv311\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000
```
```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```
