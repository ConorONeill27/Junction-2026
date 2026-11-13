# OpenAI Codex Development Workflow — WorldGit

This repository documents how **OpenAI Codex** and AI-assisted coding workflows accelerated building **WorldGit** across **6 stacked components** during Junction 2026:

1. **Meta Ray-Ban Display Web App (`glasses-app/`)**:
   - Rapidly bootstrapped against `@wearables-ui-toolkit/mrbd` design constraints (`600×600` additive display, `< 300 KB` gzipped budget, D-pad/Neural Band navigation, zero hardcoded dimensions, zero literal hex colors).
   - Generated WebMCP tool schemas (`document.modelContext`) for voice-driven `worldgit_find_object`, `worldgit_pin_memory`, and `worldgit_undo_diff`.
2. **Spatial Hub & Version Control Engine (`backend/`)**:
   - Generated FastAPI + WebSocket state synchronization, 3D bearing math (`_compute_directions`), and Reality `git diff` / `Ctrl+Z` reverse-step synthesis.
   - Wired OpenAI (`gpt-4o-mini`) in `backend/ai_services.py` as a first-class reasoning fallback alongside Google Gemini 2.5 Flash.
3. **Aiven PostgreSQL Persistence (`backend/db.py`)**:
   - Authored schema-compatible persistence for `spatial_objects`, `spatial_pins`, and `world_snapshots` with automatic SQLite fallback for offline resilience.
4. **Modal Cloud GPU Worker (`gpu_worker/reconstruct.py`)**:
   - Built Depth-Anything-V2 backprojection endpoint on Modal `L4` GPUs for live RGB-to-3D point clouds.
5. **3D Command Center (`frontend/`)**:
   - Built real-time Three.js `Points` + gaze cone + `git diff` displacement + time-travel snapshot scrubber dashboard.
