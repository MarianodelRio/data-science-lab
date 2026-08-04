---
id: T-001
phase: 0
agent: infra-agent
depends_on: []
status: pr-open
folders: [".", "src/", "tests/"]
outputs: [pyproject.toml, .env.example, ruff+mypy+pytest config, src/ package tree, tests/ tree]
size: S
branch: feature/T-001-project-scaffold
pr: "https://github.com/MarianodelRio/data-science-lab/pull/2"
---

## Project scaffold

**Scope:** repository root + empty `src/` package tree + `tests/` tree. No business logic.

**Delivers:**
- `pyproject.toml` — deps (langgraph, langchain-openai, langchain-anthropic, langchain-groq, chromadb, sentence-transformers, optuna, mlflow, kaggle, scikit-learn, xgboost, lightgbm, catboost, shap, fastapi, uvicorn) + dev deps (pytest, pytest-cov, ruff, mypy)
- `ruff.toml`, `mypy` config in pyproject, `pytest` config (`--cov=src`)
- `.env.example` with all keys from `design.md` settings.yaml (ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, GROQ_API_KEY, KAGGLE_USERNAME, KAGGLE_KEY, WORKSPACE_ROOT, LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY)
- Empty package tree: `src/{config,llm,tools,workspace,memory,observability,graph,nodes/llm,nodes/compute,api}/__init__.py`
- `tests/{unit,integration,smoke,fixtures}/` with `.gitkeep`
- `.gitignore` (Python, `runs/`, `.dt-index.json`, `chroma_data/`, `.env`)

**Done when:**
- [ ] `pip install -e ".[dev]"` exits 0
- [ ] `ruff check .` exits 0 on empty tree
- [ ] `pytest` exits 0 (no tests collected is OK)
- [ ] `.env.example` lists every key referenced in `design.md`
- [ ] README updated with setup steps
