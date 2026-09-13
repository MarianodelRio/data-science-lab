# ADR 0003 — The frontend container proxies `/api/*` same-origin via nginx, not CORS

**Date:** 2026-09-13
**Status:** Accepted
**Author:** Coder (dev-team, T-043)

## Context

T-043 wires up `docker-compose.yml` so the `frontend` and `api` services run as separate
containers. `src/api/` has no CORS middleware and no `/health` route, and T-043 does not own
`src/api/` — it must not add either. `frontend/src/api/client.ts` already assumes same-origin
(`API_BASE` defaults to `''`), and `frontend/vite.config.ts`'s dev server already proxies
`/api → http://localhost:8000` with `changeOrigin: true, ws: true`. Two containers on two ports
(`5173` for the frontend, `8000` for the api) would break that same-origin assumption unless
something reproduces the dev-time proxy in the containerized deployment.

## Decision

The frontend Docker image ships its own nginx reverse proxy mirroring `vite.config.ts`'s
dev-time `server.proxy`: `/api/*` (REST, SSE, and WebSocket traffic) is proxied same-origin to
the `api` container, and everything else falls back to `index.html` for the SPA router
(`frontend/nginx.conf`). No change to `src/api/` is required or made.

The proxy uses `resolver 127.0.0.11` (Docker's embedded DNS) plus a variable `proxy_pass`
(`set $upstream_api api:8000; proxy_pass http://$upstream_api$request_uri;`) instead of a
static `proxy_pass http://api:8000/api/;`. nginx resolves a static `proxy_pass` hostname once
at config-load time; if the `frontend` container starts before `api`'s DNS entry is registered,
nginx can fail to start entirely. The variable form defers resolution to request time, so
service start order is not a hard dependency for nginx's own startup. `$request_uri` (not
`$uri`) is used to preserve the exact original path and query string.

## Consequences

- The nginx config must be manually kept in sync if new top-level route prefixes are ever added
  outside `/api/` in `src/api/routers/` (unlikely — current routers already share the
  `/api/runs` or `/api/mlflow` prefix, both covered by the single `/api/` location).
- A future task adding CORS directly to `src/api/` would make this proxy redundant but not
  incorrect — the two approaches are not mutually exclusive.
- The frontend image now has a runtime dependency on nginx in addition to the Vite build
  toolchain, both contained in `frontend/Dockerfile`'s two build stages.
