"""SSE event emitter for live pipeline execution progress.

`EventEmitter` is a `langchain_core.callbacks.BaseCallbackHandler` subclass
that pushes one dict per graph node start/end onto a per-run `asyncio.Queue`,
consumed by the SSE endpoint in `routers/events.py`. It independently
reimplements `JsonlCallbackHandler`'s (`src/observability/jsonl_callback.py`)
node/phase-identification logic rather than subclassing it or depending on
its private members — `src/observability/` is infra-agent's folder, and
importing its public class would be fine but depending on privates would
not. See `context/discoveries/T-035.md` for the proposal to promote that
shared logic to public helpers.

`graph.invoke` runs via `asyncio.to_thread` (see T-034 decision 2), so every
hook here fires on a worker thread — never the event-loop thread that owns
the queue. `asyncio.Queue` is not thread-safe: all enqueueing goes through
`loop.call_soon_threadsafe`, never a bare `put_nowait`. `loop` is captured
via `asyncio.get_running_loop()` on the event-loop thread, at construction
time (inside `create_run`/`resume_run`).

Like `JsonlCallbackHandler`, every public hook is wrapped in
try/except-and-warn: eventing must never raise into the pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

EVENT_QUEUE_MAXSIZE: Final[int] = 1000
STREAM_END: Final[object] = object()  # sentinel; identity-checked, never serialized


def create_event_queue() -> asyncio.Queue[Any]:
    return asyncio.Queue(maxsize=EVENT_QUEUE_MAXSIZE)


def put_event_dropping_oldest(queue: asyncio.Queue[Any], item: Any) -> None:
    """Put `item` on `queue`, dropping the oldest queued item to make room if
    full, instead of raising `QueueFull`. Caller must already be on the
    event-loop thread that owns `queue` — either directly (e.g.
    `_run_and_track`'s `finally` block) or via `loop.call_soon_threadsafe`
    from a worker-thread callback hook (see `EventEmitter._emit`)."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        # If another producer refilled the queue in between, drop this item
        # silently rather than raising — a lost race with another producer.
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(item)


class EventEmitter(BaseCallbackHandler):
    """Pushes one dict per graph node start/end onto a per-run
    `asyncio.Queue`, consumed by the SSE endpoint in `routers/events.py`.

    `graph.invoke` runs via `asyncio.to_thread` (T-034 decision 2), so every
    hook here fires on a worker thread — never the event-loop thread that
    owns the queue. `asyncio.Queue` is not thread-safe: all enqueueing goes
    through `loop.call_soon_threadsafe`, never a bare `put_nowait`. `loop`
    must be captured via `asyncio.get_running_loop()` on the event-loop
    thread, at construction time (inside `create_run`/`resume_run`).

    Like `JsonlCallbackHandler`, every public hook is wrapped in
    try/except-and-warn: eventing must never raise into the pipeline.
    """

    def __init__(
        self, run_id: str, queue: asyncio.Queue[Any], loop: asyncio.AbstractEventLoop
    ) -> None:
        self.run_id = run_id
        self._queue = queue
        self._loop = loop
        self._starts: dict[UUID, dict[str, Any]] = {}
        self._skipped_chain_runs: set[UUID] = set()

    def _warn(self, exc: Exception) -> None:
        print(
            f"[EventEmitter] failed to emit event for run_id={self.run_id!r}: {exc!r}",
            file=sys.stderr,
        )

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._on_chain_start_impl(inputs, run_id=run_id, metadata=metadata, kwargs=kwargs)
        except Exception as exc:  # noqa: BLE001 - eventing must never raise
            self._warn(exc)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._on_chain_end_impl(outputs, run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            self._warn(exc)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._starts.pop(run_id, None)
            self._skipped_chain_runs.discard(run_id)
        except Exception as exc:  # noqa: BLE001
            self._warn(exc)
        # No event emitted for chain errors — mirrors JsonlCallbackHandler.
        # Failure is observable via run registry status, not this stream.

    def _on_chain_start_impl(
        self,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> None:
        name = kwargs.get("name")
        if not self._is_real_node_event(name, metadata):
            self._skipped_chain_runs.add(run_id)
            return
        node = name or (metadata or {}).get("langgraph_node") or "unknown"
        iteration = inputs.get("current_iteration") if isinstance(inputs, dict) else None
        phase = self._extract_phase(inputs, metadata)
        self._starts[run_id] = {
            "perf_start": time.perf_counter(),
            "node": node,
            "iteration": iteration,
            "phase": phase,
        }
        self._emit(
            self._build_event(
                event="start",
                node=node,
                iteration=iteration,
                phase=phase,
                duration_ms=None,
                output_summary=None,
            )
        )

    def _on_chain_end_impl(self, outputs: dict[str, Any], *, run_id: UUID) -> None:
        if run_id in self._skipped_chain_runs:
            self._skipped_chain_runs.discard(run_id)
            return
        start = self._starts.pop(run_id, None)
        if start is None:
            node, iteration, phase, duration_ms = "unknown", None, None, None
        else:
            duration_ms = round((time.perf_counter() - start["perf_start"]) * 1000)
            node, iteration, phase = start["node"], start["iteration"], start["phase"]
        self._emit(
            self._build_event(
                event="end",
                node=node,
                iteration=iteration,
                phase=phase,
                duration_ms=duration_ms,
                output_summary=self._summarize_output(outputs),
            )
        )

    @staticmethod
    def _is_real_node_event(name: Any, metadata: dict[str, Any] | None) -> bool:
        if not metadata or metadata.get("ls_integration") != "langgraph":
            return True
        return metadata.get("langgraph_node") == name

    def _extract_phase(self, inputs: Any, metadata: dict[str, Any] | None) -> Any:
        derived = self._phase_from_metadata(metadata)
        if derived is not None:
            return derived
        return inputs.get("phase") if isinstance(inputs, dict) else None

    @staticmethod
    def _phase_from_metadata(metadata: dict[str, Any] | None) -> str | None:
        if not metadata:
            return None
        checkpoint_ns = metadata.get("langgraph_checkpoint_ns")
        if not checkpoint_ns or not isinstance(checkpoint_ns, str):
            return None
        first_segment = checkpoint_ns.split("|", 1)[0]
        phase = first_segment.split(":", 1)[0]
        return phase or None

    def _summarize_output(self, outputs: Any) -> str | None:
        if not isinstance(outputs, dict) or not outputs:
            return None
        text: str | None = None
        messages = outputs.get("messages")
        if isinstance(messages, list) and messages:
            content = getattr(messages[-1], "content", None)
            if content is not None:
                text = content if isinstance(content, str) else str(content)
        if text is None:
            text = f"updated: {', '.join(sorted(outputs.keys()))}"
        text = " ".join(text.split())
        if len(text) > 200:
            text = text[:200] + "…"
        return text

    def _build_event(
        self,
        *,
        event: str,
        node: Any,
        iteration: Any,
        phase: Any,
        duration_ms: int | None,
        output_summary: str | None,
    ) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "iteration": iteration,
            "phase": phase,
            "node": node,
            "event": event,
            "duration_ms": duration_ms,
            "output_summary": output_summary,
        }

    def _emit(self, event: dict[str, Any]) -> None:
        # May raise RuntimeError if `self._loop` is already closed — caught
        # by the try/except in `on_chain_start`/`on_chain_end` above, not
        # here, matching the "every public hook is the try/except boundary"
        # convention already used by JsonlCallbackHandler.
        self._loop.call_soon_threadsafe(put_event_dropping_oldest, self._queue, event)
