---
id: T-007
phase: 1
agent: infra-agent
depends_on: [T-001]
status: available
folders: ["src/tools/"]
outputs: [kaggle_client.download, .submit, .get_score]
size: S
branch: ~
pr: ~
---

## kaggle_client tool (src/tools/kaggle_client.py)

**Scope:** `src/tools/kaggle_client.py` only.

**Delivers:**
- `download(competition, dest_dir) -> list[str]` — downloads + unzips competition data via the `kaggle` package
- `submit(competition, file_path, message) -> None`
- `get_score(competition) -> dict` — returns `{public_score: float, submitted_at: str}` from latest submission
- Auth from `KAGGLE_USERNAME`/`KAGGLE_KEY` env vars; raise a clear error if unset
- The kaggle API is injected/wrappable so tests can mock it

**Done when:**
- [ ] `download` calls the kaggle API with the competition slug and returns the list of extracted file paths (mocked)
- [ ] `submit` passes file_path and message to the API (mocked, asserted)
- [ ] `get_score` parses a mocked submission list into `{public_score, submitted_at}`
- [ ] missing credentials raise `RuntimeError` naming the missing env var
- [ ] no real network calls in tests
- [ ] `docs/pipeline.md` "Tools" section updated
