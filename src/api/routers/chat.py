"""`WS /api/runs/{id}/chat` — bidirectional chat with the read-only explainer
agent, and the channel through which a human's approve/redirect decision at
an interrupt is forwarded into the pipeline.

Binding condition (Architect, T-036): every checkpoint/workspace/RagStore
read triggered from this handler goes through `asyncio.to_thread`, and
`websocket.send_*` is only ever called from the event-loop thread (never from
inside a `to_thread` worker). Each `# thread-offload:` comment below marks a
site doing that hop, mirroring `_get_or_build_graph_values`'s comment style
in `src/api/routers/runs.py`.

Approve and redirect are dispatched on the `"type"` discriminator straight to
`do_resume` — they never reach the explainer/LLM at all; only `"question"`
does. See `docs/adr/0002-explainer-is-not-an-llmnode.md`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException

from src.api import registry
from src.api.registry import RunNotFoundError, RunRecord
from src.api.routers.runs import _config, _get_or_build_graph, do_resume
from src.workspace.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["chat"])

_VALID_TYPES = {"question", "approve", "redirect"}


def _error_frame(detail: str) -> dict[str, str]:
    return {"type": "error", "detail": detail}


def _parse_message(raw: str) -> dict[str, Any]:
    """Parse and validate one incoming chat frame.

    Raises `ValueError` (the caller turns it into an error frame) for any
    malformed payload: unparseable JSON, a non-object payload, a missing or
    unknown `"type"`, a `"question"` with a missing/non-string/blank `"text"`,
    or an `"approve"`/`"redirect"` whose present `"feedback"` is not a
    string. A *missing* `"feedback"` is not malformed — it defaults to `""`
    downstream, mirroring `ResumeRequest.feedback`'s "empty string is a
    legitimate value" semantics.
    """
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")

    message_type = message.get("type")
    if message_type not in _VALID_TYPES:
        raise ValueError(f"unknown or missing 'type': {message_type!r}")

    if message_type == "question":
        _validate_question_text(message.get("text"))
    elif "feedback" in message and not isinstance(message["feedback"], str):
        raise ValueError("'feedback' must be a string")

    return message


def _validate_question_text(text: Any) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("'text' must be a non-blank string for a 'question' message")


@router.websocket("/{run_id}/chat")
async def chat(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()

    runs_dir = websocket.app.state.runs_dir
    try:
        # thread-offload: reads run.json off the event loop.
        record = await asyncio.to_thread(registry.read_run_record, runs_dir, run_id)
    except RunNotFoundError as exc:
        await websocket.send_json(_error_frame(str(exc)))
        await websocket.close()
        return

    explainer, state_reader = await _build_explainer_session(websocket, run_id, record)

    # `_build_explainer_session` does several sequential `asyncio.to_thread`
    # calls (graph lookup, `WorkspaceManager` construction, RAG store
    # factory) that take real, unbounded wall-clock time, so `record` above
    # can be stale by now — the run may have resumed or become interrupted
    # while the session was being built. Re-read fresh right before deciding
    # whether to send the checkpoint frame, the same fresh-read pattern
    # `do_resume` (src/api/routers/runs.py) uses for its own interrupted
    # check.
    # thread-offload: reads run.json off the event loop.
    current_record = await asyncio.to_thread(registry.read_run_record, runs_dir, run_id)
    if current_record.status == "interrupted":
        # thread-offload: get_state reads the checkpoint.
        values = await asyncio.to_thread(state_reader)
        await websocket.send_json(
            {
                "type": "checkpoint",
                "phase": values.get("phase", ""),
                "summary": values.get("checkpoint_summary", ""),
            }
        )

    history: list[dict[str, str]] = []
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            message = _parse_message(raw)
        except ValueError as exc:
            await websocket.send_json(_error_frame(str(exc)))
            continue

        if message["type"] == "question":
            await _handle_question(websocket, run_id, explainer, history, message["text"])
        else:
            await _handle_resume(websocket, run_id, message)


async def _build_explainer_session(
    websocket: WebSocket, run_id: str, record: RunRecord
) -> tuple[Any, Callable[[], dict[str, Any]]]:
    """Build the per-connection `Explainer` (and its `state_reader` closure)
    for `run_id`. Every dependency it needs is fetched via `asyncio.to_thread`
    — the graph lookup (a cache-miss can hit sqlite), `WorkspaceManager`
    construction (creates the workspace directory), and the RAG store factory
    (may open/build a Chroma client)."""
    graph = await asyncio.to_thread(_get_or_build_graph, websocket, run_id)
    workspace = await asyncio.to_thread(WorkspaceManager, record.workspace_path)
    rag_store = await asyncio.to_thread(
        websocket.app.state.rag_store_factory, record.competition_name
    )

    def _state_reader() -> dict[str, Any]:
        values = graph.get_state(_config(run_id)).values
        return values if values else {}

    explainer = websocket.app.state.explainer_factory(
        workspace=workspace, state_reader=_state_reader, rag_store=rag_store
    )
    return explainer, _state_reader


async def _handle_question(
    websocket: WebSocket,
    run_id: str,
    explainer: Any,
    history: list[dict[str, str]],
    question: str,
) -> None:
    try:
        # thread-offload: covers the LLM call plus any workspace/RAG reads
        # Explainer.answer performs internally.
        answer_text = await asyncio.to_thread(explainer.answer, question, history=history)
    except Exception:
        logger.exception("explainer failed to answer a chat question for run %s", run_id)
        await websocket.send_json(_error_frame("explainer failed to answer"))
        return

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer_text})
    await websocket.send_json({"type": "answer", "text": answer_text})


async def _handle_resume(websocket: WebSocket, run_id: str, message: dict[str, Any]) -> None:
    """Forward an `"approve"`/`"redirect"` frame into `do_resume` — the same
    call for both; only the `feedback` text differs."""
    feedback = message.get("feedback", "")
    try:
        result = await do_resume(websocket, run_id, feedback)
    except HTTPException as exc:
        await websocket.send_json(_error_frame(str(exc.detail)))
        return

    await websocket.send_json({"type": "resumed", "run_id": result.run_id, "status": result.status})
