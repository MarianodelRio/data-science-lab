# Data Science Lab

A multi-agent system built on LangGraph that takes a problem statement + dataset
and produces a **complete, reproducible ML project repository**. Specialized AI
agents collaborate across seven phases — Understanding, Research, Baseline,
Design, Implementation, Evaluation, Delivery — iterating autonomously with human
checkpoints, and learning from their own experiments via a persistent RAG store.

## Prerequisites

- Python 3.10+
- pip

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in API keys and WORKSPACE_ROOT
```

## Development commands

| Command | What it does |
|---|---|
| `pytest --cov=src --cov-fail-under=70 -x` | Run tests with coverage |
| `ruff check . && ruff format --check .` | Lint + format check |
| `mypy src/` | Type check |

## Architecture

See [`design.md`](design.md) for the full system architecture and
[`plan.md`](plan.md) for the task graph.

## Documentation

- [Pipeline reference](docs/pipeline.md) — state, graph topology, the 7 phases, node classification, tools, RAG, observability, invariants
- [Agent reference](docs/agents.md) — all agents, their phase, model role, and output file
- [Configuration guide](docs/configuration.md) — settings.yaml schema, changing models, adding/removing agents, prompt versioning
- [API reference](docs/api.md) — REST, SSE, and WebSocket endpoints

## Docker / CI

### Prerequisites

- Docker Engine
- Docker Compose v2 (`docker compose version`)

### Setup

```bash
cp .env.example .env   # fill in API keys and WORKSPACE_ROOT
```

Edit `.env` and fill in the API keys you have. **`WORKSPACE_ROOT` must be an absolute path.**
Docker Compose variable substitution does **not** tilde-expand `~` — leaving the
`.env.example` placeholder `WORKSPACE_ROOT=~/competitions` unedited bind-mounts a literal
directory named `~` into the containers, not your home directory. Use something like
`WORKSPACE_ROOT=/home/you/competitions` instead.

### Run

```bash
docker compose up --build   # first run, or after changing Dockerfile.api / frontend/Dockerfile
docker compose up           # subsequent runs
docker compose down         # stop everything (add -v to also delete the chroma volume — this
                             # discards all indexed RAG data, so use it deliberately)
```

### Services

| Service | URL | Notes |
|---|---|---|
| frontend | http://localhost:5173 | The UI. Normally reach the API through this — it proxies `/api/*` same-origin to the `api` container (see `docs/adr/0003-frontend-nginx-same-origin-proxy.md`), so no CORS setup is needed |
| api | http://localhost:8000 | FastAPI backend; reachable directly for debugging, but the frontend proxy is the intended path |
| mlflow | http://localhost:5000 | Experiment tracking UI |
| chroma | localhost:8001 | RAG vector store; internal-only in normal use, exposed for debugging |

### Verify

```bash
curl http://localhost:8000/api/runs   # → [] on a clean install
```

Chroma's indexed data persists across `docker compose down` / `up` in the named
`chroma_data` volume (only `down -v` removes it).
