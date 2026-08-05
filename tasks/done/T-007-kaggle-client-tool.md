---
id: T-007
phase: 1
agent: infra-agent
depends_on: [T-001]
status: done
folders: ["src/tools/"]
outputs: [kaggle_client.download, .submit, .get_score]
size: S
branch: feature/T-007-kaggle-client-tool
pr: "https://github.com/MarianodelRio/data-science-lab/pull/9"
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
- [x] `download` calls the kaggle API with the competition slug and returns the list of extracted file paths (mocked)
- [x] `submit` passes file_path and message to the API (mocked, asserted)
- [x] `get_score` parses a mocked submission list into `{public_score, submitted_at}`
- [x] missing credentials raise `RuntimeError` naming the missing env var
- [x] no real network calls in tests
- [x] `docs/pipeline.md` "Tools" section updated

## Completed

Implemented `src/tools/kaggle_client.py`: free functions `download`/`submit`/`get_score` wrapping
`kaggle.api.kaggle_api_extended.KaggleApi`, each accepting an optional `api: KaggleApiProtocol`
parameter so tests can inject a mock. Credentials (`KAGGLE_USERNAME`/`KAGGLE_KEY`) are read directly
from `os.environ` via a local `_require_env()` helper — deliberately not routed through
`src/config/settings.py`'s `Settings`/`ApiKeysConfig` (protected contract; raises `ConfigError` at
config-load time, not the `RuntimeError`-at-call-time this task requires). `download` unzips the
downloaded archive with stdlib `zipfile` and deletes it after extraction; `get_score` selects the
submission with the latest `.date` via `max(...)`, not list order (the API doesn't document
submissions as sorted). Tests in `tests/tools/test_kaggle_client.py` (14 tests, 100% coverage, zero
network calls — verified by construction). `docs/pipeline.md` § Tools updated.

Key design finding: the `kaggle` package eagerly authenticates on import (`kaggle/__init__.py`
constructs and authenticates a `KaggleApi` at import time), so `kaggle_client.py` never imports
`kaggle` at module scope — only lazily inside `_default_api()`, after the env-var check has already
passed. Verified empirically against the installed `kaggle==1.7.4.5`/`kagglesdk==0.1.19`.

Review (code-quality, security, adversarial, smoke-test — all four run) surfaced and fixed two real
issues beyond the original spec:

- **Test gap (adversarial, confirmed by running the mutation)**: the original `get_score` "latest by
  date" test placed the correct answer at list index 0, so a regression to `submissions[0]` would
  have passed all tests unnoticed. Fixed by reordering the fixture (earlier-dated submission first)
  so date-based selection is actually distinguished from index-based selection.
- **Path validation (security + adversarial)**: `download`'s local archive path
  (`os.path.join(dest_dir, f"{competition}.zip")`) didn't validate `competition` — an absolute-path
  value would make `os.path.join` discard `dest_dir` entirely, giving attacker-chosen absolute-path
  control over what `os.remove()` deletes. Low likelihood (`competition` comes from pipeline config,
  not raw LLM/user input) but cheap to close. Fixed with `_validate_competition()` (slug-format regex)
  called at the top of all three public functions.

**Deferred, logged as an OPEN discovery in `context/discoveries.md`** (low-probability, non-blocking):
`get_score` will raise a bare `TypeError` if any submission has `date=None` (unconfirmed whether the
real API ever returns this); `download` assumes the archive is always named `{competition}.zip`,
which is a server-URL-derived filename not fully guaranteed by the `kaggle` package's own API.
