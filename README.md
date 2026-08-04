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

Not yet available — deferred to tasks T-043 and T-044.
