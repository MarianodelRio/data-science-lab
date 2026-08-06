"""JSONL execution logging callback.

`JsonlCallbackHandler` is a `langchain_core.callbacks.BaseCallbackHandler` subclass
that appends one JSON line per node entry/exit to
`{runs_dir or REPO_ROOT/"runs"}/{run_id}/execution.jsonl`. It is observability
layer 1 (always on) — see design.md § Observability for the line schema.

`runs_dir` mirrors the injectable-directory convention used by
`build_checkpointer(run_id, runs_dir=None)` in `src/graph/checkpointer.py`. This
module does not import from `src/graph/` (compute/observability code must not
depend on the graph layer) — `REPO_ROOT`/`RUNS_DIR` are derived independently
via the shared `src.config.paths.REPO_ROOT` constant.

Logging never raises into the pipeline: every public hook method catches all
exceptions and reports a one-line warning on stderr instead of propagating.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.config.paths import REPO_ROOT

RUNS_DIR: Path = REPO_ROOT / "runs"


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
        raise ValueError(f"run_id must be a non-empty path segment, got {run_id!r}")
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"run_id must not contain path separators or '..': {run_id!r}")


class JsonlCallbackHandler(BaseCallbackHandler):
    """Appends one JSON line per node start/end event to `execution.jsonl`."""

    def __init__(self, run_id: str, runs_dir: Path | None = None) -> None:
        _validate_run_id(run_id)
        self.run_id = run_id
        base_dir = runs_dir if runs_dir is not None else RUNS_DIR
        self._log_path = base_dir / run_id / "execution.jsonl"

        # Keyed by LangChain's per-callback `run_id: UUID` hook parameter.
        self._starts: dict[UUID, dict[str, Any]] = {}
        self._llm_parent: dict[UUID, UUID | None] = {}
        self._llm_model: dict[UUID, str | None] = {}
        # Keyed by the owning node's chain run id.
        self._llm_usage: dict[UUID, dict[str, Any]] = {}

    def _warn(self, exc: Exception) -> None:
        print(
            f"[JsonlCallbackHandler] failed to log event for run_id={self.run_id!r}: {exc!r}",
            file=sys.stderr,
        )

    # -- chain (node) hooks -------------------------------------------------

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
        except Exception as exc:  # noqa: BLE001 - logging must never raise
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
        except Exception as exc:  # noqa: BLE001 - logging must never raise
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
        except Exception as exc:  # noqa: BLE001 - logging must never raise
            self._warn(exc)

    # -- LLM hooks ------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._handle_llm_start(run_id, parent_run_id, invocation_params, serialized)
        except Exception as exc:  # noqa: BLE001 - logging must never raise
            self._warn(exc)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._handle_llm_start(run_id, parent_run_id, invocation_params, serialized)
        except Exception as exc:  # noqa: BLE001 - logging must never raise
            self._warn(exc)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._on_llm_end_impl(response, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - logging must never raise
            self._warn(exc)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._llm_parent.pop(run_id, None)
            self._llm_model.pop(run_id, None)
        except Exception as exc:  # noqa: BLE001 - logging must never raise
            self._warn(exc)

    # -- implementations ------------------------------------------------------

    def _on_chain_start_impl(
        self,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> None:
        node = kwargs.get("name") or (metadata or {}).get("langgraph_node") or "unknown"
        iteration = inputs.get("current_iteration") if isinstance(inputs, dict) else None
        phase = inputs.get("phase") if isinstance(inputs, dict) else None
        self._starts[run_id] = {
            "perf_start": time.perf_counter(),
            "node": node,
            "iteration": iteration,
            "phase": phase,
        }
        self._append_jsonl(
            self._build_record(
                event="start",
                node=node,
                iteration=iteration,
                phase=phase,
                duration_ms=None,
                tokens_in=None,
                tokens_out=None,
                model=None,
                output_summary=None,
            )
        )

    def _on_chain_end_impl(self, outputs: dict[str, Any], *, run_id: UUID) -> None:
        start = self._starts.pop(run_id, None)
        if start is None:
            node, iteration, phase, duration_ms = "unknown", None, None, None
        else:
            duration_ms = round((time.perf_counter() - start["perf_start"]) * 1000)
            node, iteration, phase = start["node"], start["iteration"], start["phase"]

        usage = self._llm_usage.pop(run_id, None)
        if usage:
            tokens_in, tokens_out, model = (
                usage.get("tokens_in"),
                usage.get("tokens_out"),
                usage.get("model"),
            )
        else:
            tokens_in, tokens_out, model = None, None, None

        output_summary = self._summarize_output(outputs)
        self._append_jsonl(
            self._build_record(
                event="end",
                node=node,
                iteration=iteration,
                phase=phase,
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                model=model,
                output_summary=output_summary,
            )
        )

    def _handle_llm_start(
        self,
        run_id: UUID,
        parent_run_id: UUID | None,
        invocation_params: dict[str, Any] | None,
        serialized: dict[str, Any] | None,
    ) -> None:
        self._llm_parent[run_id] = parent_run_id
        self._llm_model[run_id] = self._extract_model(invocation_params, serialized)

    def _on_llm_end_impl(self, response: LLMResult, *, run_id: UUID) -> None:
        parent = self._llm_parent.pop(run_id, None)
        model = self._llm_model.pop(run_id, None)
        tokens_in, tokens_out = self._extract_tokens(response)
        if parent is not None:
            default_bucket = {"tokens_in": 0, "tokens_out": 0, "model": None}
            bucket = self._llm_usage.setdefault(parent, default_bucket)
            bucket["tokens_in"] += tokens_in or 0
            bucket["tokens_out"] += tokens_out or 0
            bucket["model"] = bucket["model"] or model

    # -- extraction helpers -----------------------------------------------------

    def _extract_model(
        self, invocation_params: dict[str, Any] | None, serialized: dict[str, Any] | None
    ) -> str | None:
        if invocation_params:
            model = invocation_params.get("model") or invocation_params.get("model_name")
            if model:
                return str(model)
        if serialized:
            kwargs = serialized.get("kwargs") or {}
            model = kwargs.get("model") or kwargs.get("model_name")
            if model:
                return str(model)
        return None

    def _extract_tokens(self, response: LLMResult) -> tuple[int | None, int | None]:
        try:
            generation = response.generations[0][0]
            usage_metadata = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage_metadata:
                return usage_metadata.get("input_tokens"), usage_metadata.get("output_tokens")
        except (AttributeError, IndexError, KeyError):
            pass

        llm_output = response.llm_output or {}
        token_usage = llm_output.get("token_usage", {})
        if token_usage:
            tokens_in = token_usage.get("prompt_tokens", token_usage.get("input_tokens"))
            tokens_out = token_usage.get("completion_tokens", token_usage.get("output_tokens"))
            if tokens_in is not None or tokens_out is not None:
                return tokens_in, tokens_out

        return None, None

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

    def _build_record(
        self,
        *,
        event: str,
        node: Any,
        iteration: Any,
        phase: Any,
        duration_ms: int | None,
        tokens_in: int | None,
        tokens_out: int | None,
        model: str | None,
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
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": model,
            "output_summary": output_summary,
        }

    def _append_jsonl(self, record: dict[str, Any]) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
