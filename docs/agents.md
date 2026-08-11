# Agent Reference

Reference table of all LLM agents in the pipeline. Updated by each task that adds a new agent or
changes an agent's prompt/model role.

## Agents

Each agent-adding task appends one row to the table below. Do not remove or reorder existing rows.

| Agent | Pipeline phase | model_role | Output file |
|---|---|---|---|
| `data_analyst` | Phase 1 — Understanding | `reasoning` | `reports/eda_report.md` |
| `problem_framer` | Phase 1 — Understanding | `fast` | `reports/problem_definition.json` |
| `validation_strategist` | Phase 1 — Understanding | `fast` | `validation/fold_config.json` |
| `leakage_auditor` | Phase 1 — Understanding | `reasoning` | `reports/leakage_audit.json` |
| `analysis_critic` | Phase 1 — Understanding & Phase 4 — Design | `fast` | `reports/critic_verdicts_iter{iteration}.json` |
| `literature_researcher` | Phase 2 — Research | `research` | `reports/literature_research.md` |
| `web_researcher` | Phase 2 — Research | `research` | `reports/web_research.md` |
| `competition_analyst` | Phase 2 — Research | `research` | `reports/competition_analysis_iter{iteration}.md` |
| `memory_manager` | Phase 2 — Research | `fast` | `reports/memory_consolidation.md` |
| `baseline_designer` | Phase 3 — Baseline | `implementation` | `experiments/baseline/design.json` |
| `solution_architect` | Phase 4 — Design | `reasoning` | `design/iteration_{iteration}/solution_plan.json` |

## Adding an agent

1. Create `config/agents/{name}.yaml` — `model_role`, `prompt_version`, `tools`,
   `output_file_pattern`, `max_tokens` (see `AgentConfig` in `src/config/schema.py`).
2. Create `config/prompts/{name}/v1.md` with the system prompt.
3. Add `{name}` to the relevant `config/phases/{phase}.yaml`'s `nodes` and `sequence` lists.
4. Create `src/nodes/llm/{name}.py` with one class, `name` as a plain class attribute
   (not a Pydantic field — see docs/pipeline.md § Node-module convention), subclassing
   `LLMNode` (`src/nodes/llm/base.py`):

   ```python
   from src.nodes.llm.base import LLMNode

   class MyAgentNode(LLMNode):
       name = "my_agent"
   ```

   That alone is a complete node: `LLMNode.__init__` loads the `AgentConfig` and prompt and
   resolves the model via `LLMFactory`; `__call__` trims context to
   `settings.context.max_messages_per_node`, invokes the LLM, writes the response via
   `WorkspaceManager` to `output_file_pattern.format(iteration=state["current_iteration"])`,
   and returns `{"messages": [<new response>]}`. Override `_build_messages` (extra input),
   `_write_output` (structured output, e.g. `workspace.write_json`), `_resolve_output_path`
   (non-iteration placeholders), or `_build_output_state` (set the node's own `LabState`
   path field) only for non-default behavior.
5. Append a row to the table above.
