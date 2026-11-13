# WorldGit — Version Control for the Physical World

**Multiplayer Spatial Memory (`Ctrl+F`, `Ctrl+Z`, `git diff`, Spatial Pins & Friend's World) for Meta AI Glasses.**

> **Docs & AI Collaboration:**
> - [`EXPLAINER.md`](EXPLAINER.md) — Full architecture, 6-track breakdown, frozen API contracts, 4-person parallel workflow & 90s demo script
> - [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) / [`.cursorrules`](.cursorrules) — Rules for AI coding assistants working simultaneously without conflicts
> - [`CODEX_WORKFLOW.md`](CODEX_WORKFLOW.md) — OpenAI Codex architecture & verification log

## Tracks & Tech Challenges

| Track / Challenge | Integration in WorldGit | Location |
|---|---|---|
| **Meta — AI Glasses for Everyone, Everyday** | Official `@wearables-ui-toolkit/mrbd` 600×600 transparent HUD, D-pad/Neural Band controls & WebMCP voice tools | [`glasses-app/`](glasses-app/) |
| **Google DeepMind — Gemini API** | Semantic first-person frame object detection (`gemini-2.5-flash`) & `Ctrl+Z` spoken restore generation | [`backend/ai_services.py`](backend/ai_services.py) |
| **Modal — Best Use of Modal** | Cloud GPU (`L4`) monocular Depth-Anything-V2 3D point-cloud reconstruction endpoint | [`gpu_worker/reconstruct.py`](gpu_worker/reconstruct.py) |
| **ElevenLabs** | Low-latency whispered spatial guidance (`eleven_turbo_v2_5`) via `/api/tts` | [`backend/ai_services.py`](backend/ai_services.py) |
| **Aiven** | Persistent PostgreSQL backend (`AIVEN_DATABASE_URL`) for spatial objects, `git` snapshots & Friend's World memories | [`backend/db.py`](backend/db.py) |
| **OpenAI — Best Use of Codex** | AI-accelerated development workflow & GPT-4o-mini reasoning fallback | [`CODEX_WORKFLOW.md`](CODEX_WORKFLOW.md) |

## Quick Start

### 1. Backend Spatial Hub (`http://127.0.0.1:8000`)
```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Optional environment variables in `backend/.env`:
```env
GEMINI_API_KEY=...
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
AIVEN_DATABASE_URL=postgres://...
MODAL_RECONSTRUCT_URL=https://...modal.run
```

### 2. Meta Ray-Ban Display HUD (`http://127.0.0.1:4173`)
```powershell
cd glasses-app
npm run build
npm run preview -- --host 127.0.0.1 --port 4173
```
Open `http://127.0.0.1:4173/` in the **Meta Ray-Ban Display Simulator** Chrome extension (or desktop browser with Arrow keys + Enter).

### 3. Desktop 3D Command Center (`http://127.0.0.1:5173`)
```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```
