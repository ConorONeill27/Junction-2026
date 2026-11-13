# WorldGit — Claude Code Instructions (`CLAUDE.md`)

Read `AGENTS.md` and `EXPLAINER.md` before making any changes.

## Non-Negotiable Rules
1. **Ask the user which lane they own** (`glasses-app/`, `frontend/`, `backend/`, or `gpu_worker/`) and **only edit files in that lane**.
2. **Pull before editing:** Run `git pull --rebase origin main` before starting and before pushing.
3. **Keep API contracts frozen:** Never rename or remove existing JSON keys on `/api/state`, `/api/ctrl-f`, `/api/ctrl-z`, `/api/pin`, `/api/analyze-frame`, `/api/tts`, or `/ws/stream`.
4. **Preserve zero-key offline fallbacks:** Every partner integration (Gemini, OpenAI, ElevenLabs, Aiven, Modal) must still work locally with an empty `.env`.
5. **Meta Display HUD (`glasses-app/`) constraints:**
   - Keep `useWorldGit.ts` (state), `HudViews.tsx` (views), `App.tsx` (shell), `webmcp.ts` (WebMCP).
   - Max `TextStyle` is `BODY2` / `BODY2_EMPHASIZED`.
   - Never conditionally mount/unmount `<Button>` inside `<ButtonRail>`.
   - Never duplicate a field like `pin.author` across multiple visible branches in a `Panel`.
   - Keep `.content-inset` `gap: calc(var(--uit-spacing-large) + var(--uit-spacing-xsmall));` in `glasses-app/src/styles.css`.
   - Run both validators before committing `glasses-app/`:
     - `node ./meta-wearables-webapp-ref/plugins/meta-wearables-webapp/skills/ai-glasses-webapp-ui/scripts/validate-ui-toolkit.mjs ./glasses-app`
     - `node ./meta-wearables-webapp-ref/plugins/meta-wearables-webapp/skills/ai-glasses-webapp-test/scripts/check-webapp.mjs ./glasses-app --static-only`
