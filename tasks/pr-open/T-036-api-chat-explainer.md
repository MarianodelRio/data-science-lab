---
id: T-036
phase: 3
agent: api-agent
depends_on: [T-034, T-010]
status: pr-open
folders: ["src/api/", "config/agents/explainer.yaml", "config/prompts/explainer/", "docs/api.md"]
outputs: [WS /api/runs/{id}/chat, explainer agent subgraph]
size: M
branch: feature/T-036-api-chat-explainer
pr: https://github.com/MarianodelRio/data-science-lab/pull/40
---

## WebSocket chat + explainer agent (src/api/)

**Scope:** `src/api/routers/chat.py` + `src/api/explainer.py`.

**Delivers:**
- `explainer` agent: a small LangGraph subgraph / LLM node with **read-only** access to LabState, workspace files (via `WorkspaceManager`), and RagStore; answers questions and relays interrupt decisions. `model_role: reasoning`
- `WS /api/runs/{id}/chat` — bidirectional WebSocket; user messages → explainer, responses streamed back
- At an interrupt, the explainer surfaces the checkpoint summary and forwards the user's approve/redirect into `resume`
  (same forward call in both cases — "redirect" is corrective feedback text, not a different code path; it does not re-run the completed phase)

**Done when:**
- [ ] a WebSocket client sends a question and receives an explainer answer (mock LLM)
- [ ] the explainer reads workspace files through `WorkspaceManager` (asserted) and never writes
- [ ] an approve message during an interrupt triggers a resume call (asserted via fake graph)
- [ ] malformed message returns an error frame, socket stays open
- [ ] tests use the `TestClient` WebSocket + mocks, no network
- [ ] `docs/api.md` documents the chat protocol

## Completed

- Implemented per the Planner's plan and the Architect's binding conditions (ADR
  `docs/adr/0002-explainer-is-not-an-llmnode.md`):
  - `src/api/explainer.py` — `Explainer`, a plain Python class (no `LLMNode` subclass, no
    `StateGraph`). Synchronous `answer()` gathers context from `state_reader` (checkpoint
    summary + workspace report paths, read via the injected `WorkspaceManager` and
    `_relative_to_workspace`, a deliberate local duplicate of
    `src/nodes/llm/base.py::relative_to_workspace` per the ADR) and an optional
    `rag_store.query()`, builds a message list (system prompt + history + question/context),
    and calls `LLMFactory.get(config.model_role).invoke(...)`. Never calls any
    `WorkspaceManager` write method. All file/RAG read failures are caught, logged at
    `warning`, and simply omitted from context — never propagate.
  - `config/agents/explainer.yaml` (`model_role: reasoning`, `output_file_pattern: ""`) and
    `config/prompts/explainer/v1.md`.
  - `src/api/routers/runs.py` — extracted `do_resume(connection: HTTPConnection, run_id, feedback)`
    out of `resume_run`'s body byte-for-byte (same checks, same zero-`await` gap between the
    interrupted-status check and `active_runs` registration); `resume_run` is now a thin
    wrapper. Widened `_get_or_build_graph`, `_get_or_build_graph_values`, and `_has_active_run`
    from `Request` to `HTTPConnection` so the chat WebSocket handler can reuse them. Full
    existing `tests/unit/api/test_runs.py` suite passes unmodified (21/21) — zero observable
    behavior change confirmed.
  - `src/api/main.py` — added `ExplainerLike`/`ExplainerFactory` protocols, `RagStoreFactory`
    alias, `_default_explainer_factory` (returns `Explainer` directly — its keyword-only
    `__init__` matches the protocol), `_default_rag_store_factory` (lazily imports
    `RagStore`, try/except-wraps construction, returns `None` on failure so RAG unavailability
    is never fatal), new `create_app(...)` params wired to `app.state`, and registered
    `chat_router`.
  - `src/api/routers/chat.py` — `WS /api/runs/{id}/chat`: accepts, resolves the run record
    (404-equivalent error frame + close if unknown), builds/looks up the cached graph, a real
    `WorkspaceManager(record.workspace_path)`, and the injected `rag_store_factory`/
    `explainer_factory` — every one of those via `asyncio.to_thread` (commented
    `# thread-offload:` at each site, mirroring `_get_or_build_graph_values`'s comment style).
    Sends the automatic `checkpoint` frame once if the run is `"interrupted"`. Loop parses
    each incoming frame via `_parse_message` (malformed → error frame, socket stays open);
    `"question"` runs `explainer.answer` via `asyncio.to_thread` and replies with `answer`;
    `"approve"`/`"redirect"` both go straight to `do_resume` (`_handle_resume`) with only the
    `feedback` text differing, replying `resumed` or an `error` frame on `HTTPException`.
    `websocket.send_*` is only ever called from the event-loop coroutine, never from inside a
    `to_thread` worker.
  - `docs/api.md` — replaced the bare WebSocket table with the full client/server frame
    protocol description.
  - `tests/unit/api/conftest.py` — added `fake_explainer`/`explainer_factory` fixtures; `app`
    fixture now passes `explainer_factory` and `rag_store_factory=lambda _name: None` into
    `create_app`.
  - `tests/unit/api/test_explainer.py` (8 tests) and `tests/unit/api/test_chat.py` (11 tests)
    cover every "Required tests" scenario from the plan: real config/prompt load, LLM response
    passthrough, workspace-read relativization, never-writes, missing-file graceful handling,
    RAG hit inclusion, RAG failure swallowing, conversation history ordering; and for chat:
    question→answer, malformed JSON keeps socket open, missing/unknown type, missing text,
    unknown run_id closes socket, interrupted-run checkpoint-on-connect, approve/redirect both
    trigger resume with the right `feedback`, approve when not interrupted / when another run
    is active both return an error frame without closing the socket.
  - `context/discoveries/T-036.md` opened for frontend-agent with the final chat frame schema.
- Deviations from plan: None of substance. Two small, plan-compatible adaptations made while
  wiring things up:
  - `chat.py`'s message loop delegates to two small private helpers
    (`_handle_question`/`_handle_resume`) rather than inlining both branches directly in
    `chat()`, to keep `chat()` itself under ~40 lines per this project's function-size
    convention — behavior is identical to the plan's description.
  - Test workspace paths use `tmp_path`-based directories (e.g. `str(tmp_path / "workspaces" /
    run_id)`) instead of placeholder absolute paths like `/ws/titanic` used elsewhere in
    `test_runs.py` — `test_runs.py` never actually touches that path on disk, but `chat.py`
    does construct a real `WorkspaceManager(record.workspace_path)`, which would otherwise try
    to `mkdir` at the real filesystem root during tests.
- Key decisions:
  - Kept `_relative_to_workspace` as a small local duplicate in `explainer.py` rather than
    importing `src/nodes/llm/base.py::relative_to_workspace`, per the plan and ADR 0002 (keeps
    `src/api/` free of any dependency on `src/nodes/`).
  - `do_resume`'s internal `_get_or_build_graph` call stays synchronous (not `to_thread`-wrapped)
    inside `do_resume`, matching the pre-existing `resume_run` behavior exactly — wrapping it
    would introduce an `await` point between the interrupted-status check and the
    `active_runs` registration, breaking the T-034/T-035 zero-await TOCTOU guard that binding
    condition 5 requires preserving.
- Dependencies added: None (all imports — `langchain_core.messages`, `starlette.requests` —
  were already transitive dependencies of the existing stack).

### Fix-up pass (Phase 4 review findings)

- Fixed a genuine, reproducible flaky race in `tests/unit/api/test_chat.py`:
  `test_approve_triggers_resume` and `test_redirect_forwards_feedback_text` asserted on
  `fake_graph.update_state_calls[-1]` immediately after receiving the `"resumed"` frame, but
  `graph.update_state(...)` runs inside a background `asyncio.create_task`-scheduled coroutine
  that is not guaranteed to have completed by then. Added the same module-private `_wait_until`
  polling helper `tests/unit/api/test_runs.py` already uses for this exact situation, and wrapped
  the `update_state_calls` assertion in both tests with it before indexing `[-1]`. Verified with
  6 separate full runs of `tests/unit/api/test_chat.py` (11/11 passing each time) plus the full
  suite run.
- Reordered `do_resume` in `src/api/routers/runs.py`: `registry.read_run_record` and
  `_get_or_build_graph` now run via `asyncio.to_thread`, and both moved to execute *before* the
  `_has_active_run`/interrupted-status checks rather than between the checks and `active_runs`
  task registration. This satisfies binding condition 3 (every checkpoint/registry read off the
  event loop) for `do_resume`'s own reads without reopening the T-034 TOCTOU race binding
  condition 5 exists to prevent — the earlier "stays synchronous" note under Key decisions above
  is superseded by this change; see the new dated entry in `context/decisions/T-036.md` for the
  full rationale.
- `src/api/routers/chat.py`: threaded `run_id` into the `logger.exception(...)` call in
  `_handle_question` (now `logger.exception("explainer failed to answer a chat question for run
  %s", run_id)`, matching this codebase's existing %-style logging convention) so failures are
  correlatable across concurrent WebSocket connections. `_handle_question` gained a `run_id`
  parameter to carry it.
- Updated this task file's `folders:` frontmatter to the Architect's Phase-1-approved extended
  list (`["src/api/", "config/agents/explainer.yaml", "config/prompts/explainer/", "docs/api.md"]`),
  which had never been written back after approval.
- Re-verified after all changes: full suite (2220 tests, 97.19% coverage, `--cov-fail-under=70`),
  `ruff check . && ruff format --check .`, and `mypy src/` — all clean.

### Second fix-up pass (adversarial review finding, MEDIUM)

- Fixed `src/api/routers/chat.py`: the automatic checkpoint frame was gated on `record.status`
  captured *before* `_build_explainer_session` ran. That helper does several sequential
  `asyncio.to_thread` calls (graph lookup — a cache-miss `sqlite3.connect()`, `WorkspaceManager`
  construction, RAG store factory — can build a real Chroma client) that take real, unbounded
  wall-clock time, so `record.status` could be stale by the time the checkpoint-frame decision
  ran: a run resumed via the REST endpoint (or another chat connection) during session build
  could still get a stale `checkpoint` frame, or the reverse — a run that became interrupted
  during session build could miss the frame it should have gotten. Not a safety bug (`do_resume`
  re-reads fresh and correctly 409s if the client then tries to approve), but a violation of
  `docs/api.md`'s "sent only if the run is currently interrupted" contract.
  - Fix: after `_build_explainer_session` completes, re-read the run record fresh
    (`await asyncio.to_thread(registry.read_run_record, runs_dir, run_id)`) and gate the
    checkpoint-frame send on that `current_record.status`, not the `record` captured at the top
    of `chat()`. This mirrors the fresh-read pattern `do_resume` (`src/api/routers/runs.py`)
    already uses for its own interrupted-status check, and keeps `registry.read_run_record` (not
    the graph's own `snapshot.next`) as the single source of truth for run status — the two are
    populated together in `_run_and_track`/`_resume_and_track` but only the registry's `status`
    field is checked anywhere else in this router.
  - `record` (captured before session build) is still used to pass `workspace_path`/
    `competition_name` into `_build_explainer_session` — those fields don't change after a run
    is created, only `status` does, so only the status check needed the fresh read.
  - Test: added `test_checkpoint_frame_reflects_status_at_send_time_not_at_connect_time` to
    `tests/unit/api/test_chat.py`. Rather than a sleep-based timing race (this task already had
    one flaky-test incident, per the first fix-up pass above), it injects a
    `rag_store_factory` — the last blocking call `_build_explainer_session` makes — that calls
    `registry.update_run_status(tmp_path, "run-1", "running")` as a side effect, deterministically
    simulating a concurrent resume completing mid-session-build. Seeds the run `"interrupted"`,
    then asserts the first frame the client receives is the `question`'s `answer`, not a stale
    `checkpoint` frame — this would fail under the pre-fix code, which decided from the
    snapshot-before-build `record`.
  - Verified: `tests/unit/api/test_chat.py` run 3x (12/12 passing each time, no flakiness); full
    suite `pytest --cov=src --cov-fail-under=70 -x` (2221 tests, 97.17% coverage); `ruff check . &&
    ruff format --check .`; `mypy src/` — all clean.
  - Did not touch the second (LOW) adversarial finding — `WorkspaceManager.__init__`'s
    unconditional `mkdir()` on every chat connection — per explicit instruction that it's
    already reviewed and accepted as out of scope for this pass.
