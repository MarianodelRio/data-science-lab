---
id: T-012
phase: 1
agent: infra-agent
depends_on: [T-002]
status: available
folders: ["src/observability/"]
outputs: [JsonlCallbackHandler writing runs/{run_id}/execution.jsonl]
size: S
branch: ~
pr: ~
---

## JSONL logging callback (src/observability/)

**Scope:** `src/observability/` only. Observability layer 1 (always on).

**Delivers:**
- `JsonlCallbackHandler(run_id)` — a LangGraph/LangChain callback that appends one JSON line per node entry/exit to `runs/{run_id}/execution.jsonl`
- Each line matches the schema in `design.md` § Observability: `{timestamp, run_id, iteration, phase, node, event, duration_ms, tokens_in, tokens_out, model, output_summary}`
- Never raises into the pipeline: logging failures are swallowed with a stderr warning

**Done when:**
- [ ] handler writes one line on node start and one on node end
- [ ] each line is valid JSON containing all schema keys
- [ ] `duration_ms` is populated on the end event
- [ ] a write failure (e.g. bad path) does not propagate an exception
- [ ] tests use `tmp_path`
- [ ] `docs/pipeline.md` "Observability" section updated
