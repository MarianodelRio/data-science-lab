---
id: T-038
phase: 4
agent: frontend-agent
depends_on: []
status: in-progress
folders: ["frontend/"]
outputs: [React+Vite+TS app, layout shell, typed API client, dev proxy]
size: M
branch: feature/T-038-frontend-scaffold
pr: ~
---

## React scaffold + layout + API client (frontend/)

**Scope:** `frontend/` only. Independent of the Python backend — uses the API contract from `design.md`.

**Delivers:**
- Vite + React + TypeScript app with ESLint + Prettier
- Layout shell: sidebar (runs) + main area with tab slots for Pipeline / Experiments / Files / Chat
- Typed API client (`src/api/client.ts`) covering the endpoints in `design.md` § UI; base URL from env (`VITE_API_BASE`)
- Dev proxy to `localhost:8000`; responses mockable for standalone dev
- `npm run build` and `npm run lint` succeed

**Done when:**
- [ ] `npm install && npm run build` exits 0
- [ ] `npm run lint` exits 0
- [ ] the layout renders the four tab regions (component test)
- [ ] the API client exposes typed methods for runs/events/chat/submit matching `design.md`
- [ ] `.env.example` (frontend) documents `VITE_API_BASE`
- [ ] `docs/api.md` "Frontend client" note added
