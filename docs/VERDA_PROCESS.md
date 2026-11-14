# Verda Compute Track — Process & Technical Write-Up

**Project:** WorldGit — *Ctrl+F for Physical Reality on Meta AI Glasses*  
**Verda Track:** Best Use of Verda (European Renewable GPU Compute)

---

## 1. Why Verda Compute Was Essential

WorldGit's **Demo 1 (Room Note Handoff + 3D Studio)** reconstructs real indoor spaces from multi-view video frames using **VGGT-1B** (`518px` resolution, `48–80` frames per scan) so that voice notes pinned on Meta AI glasses can be raycast into persistent 3D `[x, y, z]` room coordinates (`/api/pin-from-hud`) and re-projected from new viewpoints.

Running full `48–80` frame VGGT-1B inference + dense depth/point-cloud fusion exceeds typical laptop VRAM and stalls interactive hackathon iteration. Offloading reconstruction to **Verda's 80GB A100 GPU compute in European renewable-powered data centers** unlocked:
- **Full-resolution multi-frame VGGT-1B passes** without aggressive frame dropping or downscaling.
- **Fast iteration on point-cloud geometry cleanup** (RANSAC floor-plane leveling, camera-corridor free-space carving, and statistical outlier removal) reducing `world_1` from `498,600` raw points to `281,772` clean structural points.
- **Data residency in Europe**, keeping indoor room scans and personal spatial anchors inside European infrastructure rather than opaque third-party APIs.

---

## 2. Path from Initial Idea to Working Prototype

1. **Initial Prototype (Laptop):** Tested single-room frame capture and basic YOLO-World 2D bounding boxes, but hit VRAM/latency bottlenecks on multi-frame 3D reconstruction and saw floating mid-floor noise in point clouds.
2. **Verda GPU Reconstruction Pass:** Moved VGGT-1B 3D reconstruction onto Verda A100 compute, producing aligned camera poses, depth maps, and dense room geometry (`world_1`).
3. **Geometry Cleanup & Studio Bridge:** Added RANSAC floor leveling (`shift_y=0.003`, `83,618` floor inliers), camera-corridor carving (`293,666 / 498,600` points retained), and statistical outlier removal (`281,772` final points) in `backend/static/worlds/merged/world_1.json` + `.ply`.
4. **Live Glasses ↔ 3D Studio Loop:** Connected the `:8765` real-time glasses vision server (`spatial_notes.py`) to `:8000` (`POST /api/pin-from-hud` in `backend/main.py`) so notes spoken on the glasses resolve into 3D coordinates and re-appear when another person enters the room.
5. **No-Scan Everyday Modes:** Added ORB + homography **Place Anchors** (`place_anchors.py`) and passive **Object Memory** (`object_memory.py`) with privacy pause/clear controls and TTS audio readout on the glasses APK.

---

## 3. Scaling from Hackathon Demo to Real Users

- **Verda GPU Instances / Auto-Scaling Containers:** Run VGGT-1B reconstruction jobs asynchronously per room scan while keeping lightweight `:8765` edge vision + `:8000` spatial anchor sync responsive in real time.
- **Verda Batch Jobs:** Process overnight or multi-room campus/office scans, point-cloud deduplication, and visual descriptor indexing in parallel.
- **European Privacy-First Architecture:** Users control memory capture directly from their glasses (`PRIVACY: MEMORY PAUSED` / one-tap Clear) while heavy 3D compute stays on Verda's 100% renewable European infrastructure.

---

## 4. Ready-to-Post Verda Discord Summary

> **WorldGit — Ctrl+F for Physical Reality (Meta AI Glasses + Verda A100)**  
> We used **Verda's European renewable GPU compute** to run full-resolution multi-frame **VGGT-1B** 3D room reconstruction that wouldn't fit on a hackathon laptop, then paired it with a real-time YOLO-World + ORB + object-memory pipeline on **Meta Ray-Ban Display glasses**.  
> - Person A speaks a note while looking at an object → raycast into the Verda-reconstructed 3D room (`world_1`, 281k cleaned points) → Person B walks in from another angle and sees it pinned on their HUD.  
> - Plus hands-free storefront reviews (ORB + homography) and passive `"Where is my bottle?"` object memory with TTS audio and a one-tap HUD privacy pause toggle.  
> Full technical write-up: `docs/VERDA_PROCESS.md` in our repo.
