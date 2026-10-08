# ParseFusion frontend

React 18 + TypeScript (strict) + Vite + Tailwind + React Router + TanStack Query. No mock data: every value shown comes from the backend.

## Run
```bash
npm install
cp .env.example .env     # set VITE_API_BASE_URL, e.g. http://localhost:8000
npm run dev              # http://localhost:5173
npm run build
```
`VITE_API_BASE_URL` is the backend origin (empty = the app shows a setup screen and "Backend not connected").

## Verify the wiring
Open **Agent status** and press **Run contract check**. Each of the endpoints in `src/agents/index.ts` is pinged; any envelope reply counts as connected, anything else reads `Backend not connected: <endpoint>`.

## Layout
`src/api` clients · `src/types` canonical types and zod schemas · `src/agents` 24 typed files (no logic) · `src/pages` · `src/components` · `src/hooks` · `src/lib`. Full contract: `docs/CONTRACT.md`.

## Rules the code follows
Options, roles, thresholds and limits come from `/config` and `/auth/me`; permission checks use backend `capabilities` only; the token lives in memory; only theme and density use localStorage; locked columns never receive values.
