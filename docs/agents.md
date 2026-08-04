# Agent Reference

Reference table of all LLM agents in the pipeline. Updated by each task that adds a new agent or
changes an agent's prompt/model role.

## Agents

| Agent | Pipeline phase | model_role | Output file |
|---|---|---|---|
<!-- Each agent-adding task appends one row here. Do not remove or reorder existing rows. -->

## Adding an agent

> Skeleton — populated by the first agent-adding task. Cover the concrete steps: create
> `config/agents/{name}.yaml`, create `config/prompts/{name}/v1.md`, register `{name}` in the
> relevant `config/phases/{phase}.yaml`, create `src/nodes/llm/{name}.py`, and append a row to the
> table above. See `design.md` § How to add an agent for the current draft procedure.
