# WorldGit — AI Agent & Teammate Rules (`AGENTS.md`)

Welcome to **WorldGit**! Multiple developers and AI coding assistants (Codex, Claude Code, Cursor, Antigravity, GitHub Copilot) are working on this repository simultaneously. Follow these rules strictly so nobody breaks the demo or creates merge conflicts.

---

## 1. Read First & Stay in Your Lane

Before editing any file, read `EXPLAINER.md` and ask your user **which lane** they are working on:

- **Lane 1 (`glasses-app/`):** Meta Ray-Ban Display 600×600 HUD (`@wearables-ui-toolkit/mrbd`), Neural Band / D-pad navigation, WebMCP tools (`src/webmcp.ts`).
- **Lane 2 (`frontend/`):** Desktop 3D Command Center (React + Three.js WebGL).
- **Lane 3 (`backend/`):** FastAPI Spatial Hub, WebSocket `/ws/stream`, Gemini/OpenAI/ElevenLabs integrations (`ai_services.py`), Aiven PostgreSQL (`db.py`).
- **Lane 4 (`gpu_worker/`):** Modal `L4` GPU `Depth-Anything-V2` reconstruction (`reconstruct.py`).

**Rules:**
1. **Only edit files inside your assigned lane** unless the user explicitly approves a cross-lane change.
2. **Sync before & after:** Always run `git pull --rebase origin main` before starting work and before pushing.
3. **Never commit secrets or generated artifacts:** Keep `.env`, `node_modules/`, `.venv/`, `dist/`, `.wearables-test/`, and `*.db` untracked.

---

## 2. Frozen API & WebSocket Contracts

Do **not** rename, remove, or change the types of existing JSON fields in:
- `GET /api/state`
- `POST /api/ctrl-f`
- `GET /api/ctrl-z`
- `POST /api/pin`
- `POST /api/analyze-frame`
- `POST /api/tts`
- `WS /ws/stream`

You may add **new optional fields**, provided existing fields remain 100% backward-compatible for `glasses-app/` and `frontend/`.

---

## 3. Offline-First & Zero-Key Fallbacks Are Mandatory

Hackathon venue Wi-Fi and API quotas can fail at any moment. Every external integration **must** degrade gracefully without crashing startup:
- `GEMINI_API_KEY` missing → deterministic mock object detection & `Ctrl+Z` instructions.
- `OPENAI_API_KEY` missing → fallback skipped cleanly.
- `ELEVENLABS_API_KEY` missing → `/api/tts` returns `{ "fallback": "browser_speech_synthesis", "text": ... }` and UI speaks via `window.speechSynthesis`.
- `AIVEN_DATABASE_URL` missing → `backend/db.py` uses local SQLite (`worldgit_aiven_cache.db`).
- `MODAL_RECONSTRUCT_URL` missing → `backend/main.py` uses the pre-seeded procedural 3D point cloud.

Never require an API key at import time or server startup.

---

## 4. Mandatory Rules for `glasses-app/` (Meta Ray-Ban Display HUD)

`glasses-app/` is checked by Meta's official static & UI toolkit validators (`meta-wearables-webapp-ref`). Breaking any of these rules will fail validation:

1. **Preserve the 4-file architecture:**
   - `src/useWorldGit.ts` — all domain state, WebSocket/REST hooks, and actions.
   - `src/HudViews.tsx` — UI panels and views.
   - `src/App.tsx` — thin navigation/router shell only (never make `App.tsx` monolithic).
   - `src/webmcp.ts` — `document.modelContext` WebMCP tool registrations.
2. **Typography ceiling:** Never use a `TextStyle` larger than `TextStyle.BODY2` or `TextStyle.BODY2_EMPHASIZED` in routine HUD panels (no `HEADING1` or `HEADING2`).
3. **Static `<ButtonRail>` children:** Never conditionally mount/unmount `<Button>` elements inside a `<ButtonRail>` (breaks D-pad/Neural Band focus handoff). Keep the `<Button>` slots static and change labels/handlers instead.
4. **No duplicate item fields in a `Panel`:** Never render the same field (e.g., `pin.author`) in multiple visible branches inside the same `Panel`.
5. **Preserve `.content-inset` spacing:** In `glasses-app/src/styles.css`, keep `gap: calc(var(--uit-spacing-large) + var(--uit-spacing-xsmall));`.
6. **WebMCP `.description` rendering:** Because `src/webmcp.ts` defines `description:` fields, at least one `.tsx` file must render a `.description` property (currently `{VOICE_HINTS.ctrlf.description}` in `HudViews.tsx`).
7. **Always run both Meta validators after editing `glasses-app/`:**
   ```powershell
   cd glasses-app
   npm run build
   cd ..
   node ./meta-wearables-webapp-ref/plugins/meta-wearables-webapp/skills/ai-glasses-webapp-ui/scripts/validate-ui-toolkit.mjs ./glasses-app
   node ./meta-wearables-webapp-ref/plugins/meta-wearables-webapp/skills/ai-glasses-webapp-test/scripts/check-webapp.mjs ./glasses-app --static-only
   ```
   Both must pass with **0 findings** and `"passed": true` before committing.

---

## 5. Verification Checklist Before Pushing

- **If you edited `glasses-app/`:** `npm run build`, `validate-ui-toolkit.mjs` (0 findings), `check-webapp.mjs --static-only` (`passed: true`).
- **If you edited `frontend/`:** `npm run build` succeeds with 0 errors.
- **If you edited `backend/`:** Start `uvicorn main:app` with no `.env` keys and verify `GET /api/state` and `GET /api/ctrl-z` return HTTP 200.
- **If you edited `gpu_worker/`:** Verify Python syntax (`python -m py_compile gpu_worker/reconstruct.py`).
