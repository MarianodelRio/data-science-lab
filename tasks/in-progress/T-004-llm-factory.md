---
id: T-004
phase: 0
agent: infra-agent
depends_on: [T-003]
status: in-progress
folders: ["src/llm/"]
outputs: [LLMFactory.get(role), provider wrappers for anthropic/deepseek/groq/gemini/openai]
size: M
branch: feature/T-004-llm-factory
pr: ~
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
- [ ] `LLMFactory.get("reasoning")` returns a `ChatOpenAI` pointed at `api.deepseek.com` (assert base_url, no network)
- [ ] `LLMFactory.get("advisor")` returns a `ChatAnthropic` with model `claude-opus-5`
- [ ] unknown role raises `KeyError`; unknown provider raises `ValueError`
- [ ] provider clients are mocked in tests — no real API calls
- [ ] `mypy src/llm/` passes
- [ ] `docs/configuration.md` "Changing a model" section updated
