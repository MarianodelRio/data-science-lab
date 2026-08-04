---
id: T-037
phase: 3
agent: api-agent
depends_on: [T-034, T-007]
status: blocked
folders: ["src/api/"]
outputs: [POST /api/runs/{id}/submit, GET /api/mlflow/url]
size: S
branch: ~
pr: ~
---

## Kaggle submit + MLflow URL endpoints (src/api/)

**Scope:** `src/api/routers/kaggle.py` + `src/api/routers/mlflow.py`.

**Delivers:**
- `POST /api/runs/{id}/submit` — triggers a Kaggle submission of the run's best submission via the `kaggle_client` tool; returns `{public_score}`
- `GET /api/mlflow/url` — returns the MLflow tracking UI URL (the Docker `mlflow` service, e.g. `http://localhost:5000`) for the frontend button

**Done when:**
- [ ] `POST /api/runs/{id}/submit` calls the tool's `submit`+`get_score` (mocked) and returns `{public_score}`
- [ ] submit on a run without a best submission returns 409 with a clear message
- [ ] `GET /api/mlflow/url` returns the configured MLflow URL from settings
- [ ] tests use `TestClient` + mocked kaggle tool, no network
- [ ] `docs/api.md` documents both endpoints
