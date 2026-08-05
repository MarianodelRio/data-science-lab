---
id: T-004
phase: 0
agent: infra-agent
depends_on: [T-003]
status: pr-open
folders: ["src/llm/"]
outputs: [LLMFactory.get(role), provider wrappers for anthropic/deepseek/groq/gemini/openai]
size: M
branch: feature/T-004-llm-factory
pr: "https://github.com/MarianodelRio/data-science-lab/pull/8"
---

## LLMFactory + provider wrappers (src/llm/)

**Scope:** `src/llm/` only. **Shared contract** — sole point of LLM instantiation.

**Delivers:**
- `LLMFactory.get(role: str) -> BaseChatModel` — reads `Settings.models[role]`, dispatches by provider (see `design.md` § LLMFactory API)
- Provider wrappers: `anthropic → ChatAnthropic`, `deepseek → ChatOpenAI(base_url="https://api.deepseek.com")`, `groq → ChatGroq`, `gemini → ChatGoogleGenerativeAI`, `openai → ChatOpenAI`
- Each wrapper applies model, temperature, max_tokens from config
- Unknown provider raises `ValueError` naming the provider
- No network calls at import time

**Done when:**
- [x] `LLMFactory.get("reasoning")` returns a `ChatOpenAI` pointed at `api.deepseek.com` (assert base_url, no network)
- [x] `LLMFactory.get("advisor")` returns a `ChatAnthropic` with model `claude-opus-5`
- [x] unknown role raises `KeyError`; unknown provider raises `ValueError`
- [x] provider clients are mocked in tests — no real API calls
- [x] `mypy src/llm/` passes
- [x] `docs/configuration.md` "Changing a model" section updated

## Completed

Implemented `LLMFactory.get(role: str) -> BaseChatModel` in `src/llm/factory.py` as the
sole point of LLM instantiation, per `design.md`'s approved API. Role→`ModelRoleConfig`
and provider→builder resolution both use explicit `dict[str, Callable[...]]` mappings
(not `getattr`/if-elif) so an unknown role raises `KeyError(role)` and an unknown
provider raises `ValueError` naming it, satisfying the Done-when criteria exactly.
All 5 provider wrappers (anthropic/deepseek/groq/gemini/openai) implemented, each
applying `model`/`temperature`/conditionally-`max_tokens` from config; `api_key` kwargs
wrapped in `SecretStr` where `ApiKeysConfig` provides one (anthropic/deepseek/groq) —
`openai`/`gemini` have no dedicated `ApiKeysConfig` field, so those two rely on the
underlying LangChain SDK's own env-var fallback (documented as an open discovery for
whichever future task routes a role to one of them).

`LLMFactory` lazily caches a `Settings` singleton on the first `.get()` call (class-level
`ClassVar[Settings | None]`), closing the open question T-003's decision log explicitly
left for this task, instead of re-parsing `config/settings.yaml` on every call.

Added `langchain-google-genai` to `pyproject.toml` dependencies (was missing). Updated
`docs/configuration.md` "Changing a model" section to document the caching behavior and
full role→provider dispatch table. Logged the caching + explicit-mapping design decision
in `context/decisions.md`; flagged the `openai`/`gemini` `ApiKeysConfig` gap in
`context/discoveries.md` for future tasks.

Reviewed: code-quality APPROVED (no blockers), security CLEAN, smoke tests 6/6 PASS,
mutation testing 100% (13/13 killed, threshold 80% — critical module). Adversarial review
found no exploitable issues but flagged one protected-contract doc drift: `design.md`'s
`LLMFactory.get` snippet specifies `@staticmethod`; the implementation uses `@classmethod`
(required for the `cls._settings` cache) — behaviorally transparent to every caller, but
`design.md` needs a one-line sync as a fast-follow. Two optional test additions
(concurrency test for the first-call cache race — analyzed and confirmed benign since
`Settings.load()` is pure/immutable; a test that a failed `Settings.load()` doesn't
poison the cache) and an optional fail-fast config-validation were suggested but deferred
as non-blocking, not required by this task's Done-when checklist.
