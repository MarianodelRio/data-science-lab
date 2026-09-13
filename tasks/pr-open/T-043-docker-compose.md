---
id: T-043
phase: 5
agent: infra-agent
depends_on: [T-034, T-038]
status: pr-open
folders: ["docker/", ".", "frontend/"]
outputs: [docker-compose.yml, Dockerfile.api, frontend/Dockerfile, chroma + mlflow services]
size: M
branch: feature/T-043-docker-compose
pr: https://github.com/MarianodelRio/data-science-lab/pull/43
---

## docker-compose + Dockerfiles + services

**Scope:** `docker-compose.yml`, `Dockerfile.api`, `frontend/Dockerfile`, related docker assets.

**Delivers:**
- Four services exactly as in `design.md` § Docker deployment: `api`, `frontend`, `chroma`, `mlflow`
- Workspace bind mount (`${WORKSPACE_ROOT}:/competitions`), `runs/` + `config/` mounts on api, named volume for chroma
- Healthcheck on chroma; `api` depends_on chroma healthy + mlflow started
- Env wired from `.env`

**Done when:**
- [x] `docker compose config` validates with no errors
- [x] `docker compose build` succeeds for api and frontend images
- [x] `docker compose up` brings all four services up, chroma healthy; `GET localhost:8000/api/runs` returns 200 (`[]`) and `localhost:5173` serves the UI — Architect-approved adjustment: `/health` does not exist in `src/api/main.py` and is outside this task's `folders:`, so `GET /api/runs` is the smoke check instead (see `## Completed` below)
- [x] chroma data persists across `down`/`up` (named volume)
- [x] MLflow UI reachable at `localhost:5000`
- [x] `README.md` run instructions updated

## Completed
- What was implemented: the full four-service `docker-compose.yml` (`api`, `frontend`, `chroma`, `mlflow`) plus `Dockerfile.api` (repo root) and `frontend/Dockerfile` + `frontend/nginx.conf` (multi-stage Node build → nginx runtime). The frontend container reverse-proxies `/api/*` same-origin to the `api` container (WebSocket upgrade + SSE-friendly `proxy_buffering off`/long `proxy_read_timeout`), since `src/api/` has no CORS middleware and is outside this task's ownership — documented in `docs/adr/0003-frontend-nginx-same-origin-proxy.md`. Added root and `frontend/` `.dockerignore` files, pre-cached the `all-MiniLM-L6-v2` sentence-transformers model into the api image, pinned `chroma`/`mlflow` to concrete image tags (`chromadb/chroma:1.5.9`, `ghcr.io/mlflow/mlflow:v2.22.4`), fixed `.gitignore`'s `runs/` exclusion to `runs/*` + `!runs/.gitkeep` so the bind-mount source directory is host-owned before first `up`, and replaced `README.md`'s "Docker / CI — Not yet available" section with real setup/run/verify instructions (including the `WORKSPACE_ROOT` tilde-expansion gotcha).
  Full verification was run for real (not just inspected): `docker compose config` (clean), `docker compose build` (both images built via compose, reusing/rebuilding as needed), `docker compose up -d` (all four containers started, chroma reported `healthy`), `curl http://localhost:8000/api/runs` → `200 []`, `curl -I http://localhost:5173` → `200` (nginx serving the SPA), `curl -I http://localhost:5000` → `200` (MLflow UI), and `curl http://localhost:5173/api/runs` → `200` through the nginx proxy itself, confirming the same-origin design works end to end. Persistence: noted the `chroma_data` volume name, ran `docker compose down` (no `-v`), `docker compose up -d` again — same volume reused, chroma healthy again immediately. All test containers/volumes were torn down (`docker compose down -v`) and scratch files (`.env`, a temporary `WORKSPACE_ROOT` dir) removed afterward; nothing was left running.
- Deviations from plan: (1) The task's original Done-when checklist referenced `GET localhost:8000/health`, which does not exist in `src/api/main.py` (out of this task's folder ownership to add) — Architect-approved in Phase 1 to use `GET /api/runs` instead. (2) The `chroma` healthcheck in design.md/the plan (`curl -f http://localhost:8000/api/v2/heartbeat`) failed on the very first real `docker compose up` — `chromadb/chroma:1.5.9` ships neither `curl`, `wget`, nor a `python` interpreter on `$PATH` (verified empirically via `docker exec`). Replaced with a `bash`-only probe using `/dev/tcp` (bash is present in the image) sending a raw HTTP/1.1 GET and checking for `200 OK`. This is exactly the risk the Architect flagged during analysis (design.md's snippet was unverified against the actual pinned image).
- Key decisions: pinned `chroma`/`mlflow` server image tags rather than editing `pyproject.toml`'s unpinned `chromadb`/`mlflow` versions (out of this task's scope; logged as an open discovery for a future task instead — client/server version drift risk). `frontend/nginx.conf` uses a `resolver 127.0.0.11` + variable `proxy_pass` (`set $upstream_api api:8000; proxy_pass http://$upstream_api$request_uri;`) instead of a static `proxy_pass`, so nginx defers hostname resolution to request time rather than failing to start if `frontend` boots before `api`'s DNS entry exists. `Dockerfile.api` uses `pip install .` (non-editable) since the image is a frozen deployable artifact, not a dev environment. `config/` is both bind-mounted at runtime (source of truth) and baked into the api image (zero-cost fallback if ever run without the mount).
- Dependencies added: None to `pyproject.toml`/`package.json` — only new Docker-layer installs (`libgomp1` via apt in `Dockerfile.api`, required for xgboost/lightgbm/catboost to import correctly on `python:3.11-slim`).
