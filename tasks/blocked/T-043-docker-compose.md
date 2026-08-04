---
id: T-043
phase: 5
agent: infra-agent
depends_on: [T-034, T-038]
status: blocked
folders: ["docker/", ".", "frontend/"]
outputs: [docker-compose.yml, Dockerfile.api, frontend/Dockerfile, chroma + mlflow services]
size: M
branch: ~
pr: ~
---

## docker-compose + Dockerfiles + services

**Scope:** `docker-compose.yml`, `Dockerfile.api`, `frontend/Dockerfile`, related docker assets.

**Delivers:**
- Four services exactly as in `design.md` § Docker deployment: `api`, `frontend`, `chroma`, `mlflow`
- Workspace bind mount (`${WORKSPACE_ROOT}:/competitions`), `runs/` + `config/` mounts on api, named volume for chroma
- Healthcheck on chroma; `api` depends_on chroma healthy + mlflow started
- Env wired from `.env`

**Done when:**
- [ ] `docker compose config` validates with no errors
- [ ] `docker compose build` succeeds for api and frontend images
- [ ] `docker compose up` brings all four services healthy; `GET localhost:8000/health` returns 200 and `localhost:5173` serves the UI
- [ ] chroma data persists across `down`/`up` (named volume)
- [ ] MLflow UI reachable at `localhost:5000`
- [ ] `README.md` run instructions updated
