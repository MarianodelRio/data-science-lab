"""Unit tests for `EventEmitter` and `put_event_dropping_oldest`.

No `pytest-asyncio` dependency in this project — async assertions run inside
a plain `asyncio.run(...)` call from an otherwise-sync test function, the
same pattern used throughout `tests/unit/api/` for background-task polling.
"""

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

from src.api.event_emitter import EventEmitter, create_event_queue, put_event_dropping_oldest

SCHEMA_KEYS = {
    "timestamp",
    "run_id",
    "iteration",
    "phase",
    "node",
    "event",
    "duration_ms",
    "output_summary",
}


def _make_emitter(run_id: str = "run-1") -> tuple[EventEmitter, "asyncio.Queue"]:
    """Build an `EventEmitter` bound to the currently-running event loop.
    Must be called from inside a coroutine (i.e. within `asyncio.run(...)`)
    so `asyncio.get_running_loop()` resolves."""
    loop = asyncio.get_running_loop()
    queue = create_event_queue()
    return EventEmitter(run_id, queue, loop), queue


def _make_offline_emitter(run_id: str = "run-1") -> EventEmitter:
    """Build an `EventEmitter` for tests that only exercise pure logic
    (e.g. `_summarize_output`) and never touch the queue/loop."""
    loop = asyncio.new_event_loop()
    return EventEmitter(run_id, asyncio.Queue(), loop)


def test_start_then_end_same_thread_produces_two_ordered_events_with_correct_fields() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()
        inputs = {"current_iteration": 1, "phase": "phase3_baseline"}

        emitter.on_chain_start({}, inputs, run_id=run_id, name="node_a")
        emitter.on_chain_end({"messages": []}, run_id=run_id)

        start_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        end_event = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert set(start_event) == SCHEMA_KEYS
        assert start_event["event"] == "start"
        assert start_event["node"] == "node_a"
        assert start_event["iteration"] == 1
        assert start_event["phase"] == "phase3_baseline"
        assert start_event["duration_ms"] is None
        assert start_event["output_summary"] is None

        assert set(end_event) == SCHEMA_KEYS
        assert end_event["event"] == "end"
        assert end_event["node"] == "node_a"
        assert isinstance(end_event["duration_ms"], int)
        assert end_event["duration_ms"] >= 0

    asyncio.run(scenario())


def test_cross_thread_delivery_via_call_soon_threadsafe() -> None:
    """Drives the hooks from a real `threading.Thread` (never the loop
    thread) and reads back from the test's own event loop — proves the
    `call_soon_threadsafe` wiring, not just same-thread behavior."""

    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()

        def drive() -> None:
            emitter.on_chain_start({}, {}, run_id=run_id, name="node_a")
            emitter.on_chain_end({}, run_id=run_id)

        thread = threading.Thread(target=drive)
        thread.start()
        thread.join()

        start_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        end_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert start_event["event"] == "start"
        assert end_event["event"] == "end"

    asyncio.run(scenario())


def test_langgraph_internal_plumbing_run_emits_no_event() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()
        metadata = {"ls_integration": "langgraph", "langgraph_node": "phase1_understanding"}

        emitter.on_chain_start({}, {}, run_id=run_id, name="LangGraph", metadata=metadata)
        emitter.on_chain_end({}, run_id=run_id)

        await asyncio.sleep(0)
        assert queue.empty()

    asyncio.run(scenario())


def test_real_node_run_with_langgraph_metadata_emits_event() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()
        metadata = {"ls_integration": "langgraph", "langgraph_node": "problem_framer"}

        emitter.on_chain_start({}, {}, run_id=run_id, name="problem_framer", metadata=metadata)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["node"] == "problem_framer"
        assert event["event"] == "start"

    asyncio.run(scenario())


def test_phase_derived_from_checkpoint_ns_overrides_inputs_phase() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()
        metadata = {"langgraph_checkpoint_ns": "phase2_research:abc123|researcher:def456"}
        inputs = {"phase": "phase1_understanding"}

        emitter.on_chain_start({}, inputs, run_id=run_id, name="researcher", metadata=metadata)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["phase"] == "phase2_research"

    asyncio.run(scenario())


def test_phase_falls_back_to_inputs_phase_when_no_metadata() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()
        inputs = {"phase": "phase3_baseline"}

        emitter.on_chain_start({}, inputs, run_id=run_id, name="node_a")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["phase"] == "phase3_baseline"

    asyncio.run(scenario())


def test_chain_end_without_prior_start_uses_defensive_fallback() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()

        emitter.on_chain_end({}, run_id=run_id)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["node"] == "unknown"
        assert event["iteration"] is None
        assert event["phase"] is None
        assert event["duration_ms"] is None

    asyncio.run(scenario())


def test_chain_error_clears_bookkeeping_and_emits_nothing() -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()

        emitter.on_chain_start({}, {}, run_id=run_id, name="node_a")
        await asyncio.wait_for(queue.get(), timeout=1.0)  # drain the start event
        assert run_id in emitter._starts

        emitter.on_chain_error(RuntimeError("boom"), run_id=run_id)

        assert run_id not in emitter._starts
        await asyncio.sleep(0)
        assert queue.empty()

    asyncio.run(scenario())


def test_summarize_output_truncates_long_message_content() -> None:
    emitter = _make_offline_emitter()
    long_content = "a" * 250

    summary = emitter._summarize_output({"messages": [SimpleNamespace(content=long_content)]})

    assert summary == "a" * 200 + "…"


def test_summarize_output_falls_back_to_updated_keys_when_no_messages() -> None:
    emitter = _make_offline_emitter()

    summary = emitter._summarize_output({"score": 0.9, "phase": "phase3_baseline"})

    assert summary == "updated: phase, score"


def test_summarize_output_returns_none_for_empty_or_non_dict_outputs() -> None:
    emitter = _make_offline_emitter()

    assert emitter._summarize_output({}) is None
    assert emitter._summarize_output(None) is None
    assert emitter._summarize_output("not a dict") is None


def test_loop_failure_is_swallowed_and_warned_on_stderr(capsys) -> None:
    async def scenario() -> None:
        emitter, queue = _make_emitter()
        run_id = uuid4()

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("loop closed")

        emitter._loop.call_soon_threadsafe = _raise  # type: ignore[method-assign]

        emitter.on_chain_start({}, {}, run_id=run_id, name="node_a")  # must not raise

        err = capsys.readouterr().err
        assert "EventEmitter" in err
        assert "loop closed" in err
        assert queue.empty()

    asyncio.run(scenario())


def test_put_event_dropping_oldest_drops_oldest_when_full() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)

        put_event_dropping_oldest(queue, "a")
        put_event_dropping_oldest(queue, "b")
        put_event_dropping_oldest(queue, "c")

        assert queue.qsize() == 2
        first = await queue.get()
        second = await queue.get()
        assert [first, second] == ["b", "c"]
        assert queue.empty()

    asyncio.run(scenario())
