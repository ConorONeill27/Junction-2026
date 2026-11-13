# WorldGit — GitHub Copilot Instructions

Read `EXPLAINER.md` and `AGENTS.md` before suggesting or applying edits.

- **Lane Isolation:** Only modify files in the user's assigned lane (`glasses-app/`, `frontend/`, `backend/`, or `gpu_worker/`).
- **Frozen Contracts:** Keep all existing JSON keys in `/api/state`, `/api/ctrl-f`, `/api/ctrl-z`, `/api/pin`, `/api/analyze-frame`, `/api/tts`, and `/ws/stream` backward-compatible.
- **Offline Fallbacks:** Never require `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `AIVEN_DATABASE_URL`, or `MODAL_RECONSTRUCT_URL` at startup; preserve all local fallbacks.
- **Meta Display HUD (`glasses-app/`):**
  - Maintain the split across `useWorldGit.ts`, `HudViews.tsx`, `App.tsx`, and `webmcp.ts`.
  - Keep `TextStyle <= BODY2_EMPHASIZED`, keep `<ButtonRail>` children static, avoid repeating fields like `pin.author` in a `Panel`, and keep `.content-inset` `gap: calc(var(--uit-spacing-large) + var(--uit-spacing-xsmall));` in `styles.css`.
  - Validate edits with `validate-ui-toolkit.mjs` and `check-webapp.mjs --static-only` inside `meta-wearables-webapp-ref/`.
