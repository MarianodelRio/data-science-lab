"""feature_engineer: reads the solution plan + EDA report and designs feature
transformations (encodings, null handling, interactions), writing
design/iteration_{iteration}/feature_spec.json.

Overrides `_build_messages` (inject the solution plan and EDA report as an
extra HumanMessage), `_write_output` (extract + validate the JSON payload,
write it via `workspace.write_json`), AND `_build_output_state` — unlike
`baseline_designer`, this node DOES set a new `LabState` field
(`feature_spec_path`): it is load-bearing for
`analysis_critic._detect_phase_stem`, which distinguishes a Phase 1
(Understanding) critic pass from a Phase 4 (Design) critic pass by checking
whether `feature_spec_path` has been written yet.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager


def _strip_outer_fence(content: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-anchoring approach as `baseline_designer._strip_outer_fence`/
    `problem_framer._strip_outer_fence`/`leakage_auditor._strip_outer_fence`: anchors
    on the outermost ``` markers only, so an embedded ``` inside a string value (e.g.
    a hyperparameter description quoting code) is never mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError("feature_engineer response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError("feature_engineer response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError("feature_engineer response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _extract_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from the LLM response.

    Accepts: raw JSON with no fence, or the entire response wrapped in a
    single ```json or unlabeled ``` fence. Invalid JSON raises a clear
    ValueError naming 'feature_engineer'.
    """
    text = _strip_outer_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"feature_engineer response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"feature_engineer response must be a JSON object, got {type(data).__name__}"
        )
    return data


def _read_solution_plan(state: LabState, workspace: WorkspaceManager) -> str:
    """Read state['solution_plan_path'] as pretty-printed JSON text. Degrades
    to a placeholder, never raises — mirrors baseline_designer._read_problem_definition.
    T-021 (solution_architect) may not have run yet, so this path is legitimately
    unset during standalone/partial-phase execution."""
    path = state.get("solution_plan_path") or ""
    if not path:
        return "(solution plan not yet available)"
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
    except OSError:
        return f"(unable to read solution plan at {path})"
    return json.dumps(data, indent=2)


def _read_eda_report(state: LabState, workspace: WorkspaceManager) -> str:
    """Same behavior as baseline_designer._read_eda_report — own copy,
    per-module duplication is the established convention for these reader
    helpers (only relative_to_workspace itself was hoisted, in T-020)."""
    path = state.get("eda_report_path") or ""
    if not path:
        return "(EDA report not yet available)"
    try:
        return workspace.read_text(relative_to_workspace(path, workspace))
    except OSError:
        return f"(unable to read EDA report at {path})"


# Curated set of method names/phrases that denote the target-encoding *family* of
# techniques — every one of these computes a per-category statistic derived from the
# target column, so every one of them leaks target information across CV folds unless
# it is computed per-fold. Deliberately does NOT include bare "target" as a keyword: a
# method name can legitimately mention "target" without being target encoding (e.g.
# "frequency_encoding_excluding_target_leak"), so a bare substring match on "target" is
# both under-inclusive (misses category_encoders-style names below that never say
# "target" at all, e.g. "leave_one_out", "WOE", "catboost") and over-inclusive (flags
# unrelated methods that merely mention "target"). Matched as whole phrases with word
# boundaries against a separator-normalized (- and _ collapsed to spaces) copy of the
# method string, so "target_encoding" / "target-encoding" / "target encoding" and
# similar variants of every phrase below all match uniformly.
_TARGET_ENCODING_KEYWORDS = (
    "target encoding",
    "target mean",
    "smoothed target",
    "mean encoding",
    "leave one out",
    "loo",
    "woe",
    "weight of evidence",
    "catboost",
    "james stein",
    "m estimate",
    "impact encoding",
)


def _is_target_encoding_method(method: str) -> bool:
    """Whole-phrase match against `_TARGET_ENCODING_KEYWORDS`, robust to LLM phrasing
    variance across the standard `category_encoders`-library names for the
    target-encoding family (target_encoding, mean_target_encoding, mean_encoding,
    leave_one_out, WOE, CatBoost encoding, James-Stein, M-estimate, impact_encoding,
    ...) while not flagging an unrelated method that merely mentions the word "target"
    (e.g. "frequency_encoding_excluding_target_leak")."""
    normalized = re.sub(r"[-_]+", " ", method.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return any(
        re.search(rf"\b{re.escape(keyword)}\b", normalized) for keyword in _TARGET_ENCODING_KEYWORDS
    )


def _validate_encodings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(
            f"feature_engineer response missing required list field 'encodings', got {value!r}"
        )
    result: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(
                f"feature_engineer response 'encodings[{i}]' must be an object, got {item!r}"
            )
        column = item.get("column")
        if not isinstance(column, str) or not column.strip():
            raise ValueError(
                f"feature_engineer response 'encodings[{i}]' missing required non-empty "
                "string field 'column'"
            )
        method = item.get("method")
        if not isinstance(method, str) or not method.strip():
            raise ValueError(
                f"feature_engineer response 'encodings[{i}]' missing required non-empty "
                "string field 'method'"
            )
        entry: dict[str, Any] = {"column": column, "method": method}
        if _is_target_encoding_method(method):
            # Fold-aware target encoding is a hard requirement (T-022 done-when):
            # plain target encoding leaks target information across CV folds.
            if item.get("fold_aware") is not True:
                raise ValueError(
                    f"feature_engineer response 'encodings[{i}]' uses target encoding on "
                    f"column {column!r} but does not set 'fold_aware': true — fold-aware "
                    "target encoding is required whenever target encoding is used"
                )
            entry["fold_aware"] = True
        result.append(entry)
    return result


def _validate_null_handling(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(
            f"feature_engineer response missing required list field 'null_handling', got {value!r}"
        )
    result: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(
                f"feature_engineer response 'null_handling[{i}]' must be an object, got {item!r}"
            )
        column = item.get("column")
        if not isinstance(column, str) or not column.strip():
            raise ValueError(
                f"feature_engineer response 'null_handling[{i}]' missing required non-empty "
                "string field 'column'"
            )
        strategy = item.get("strategy")
        if not isinstance(strategy, str) or not strategy.strip():
            raise ValueError(
                f"feature_engineer response 'null_handling[{i}]' missing required non-empty "
                "string field 'strategy'"
            )
        result.append({"column": column, "strategy": strategy})
    return result


def _validate_interactions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(
            f"feature_engineer response missing required list field 'interactions', got {value!r}"
        )
    result: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(
                f"feature_engineer response 'interactions[{i}]' must be an object, got {item!r}"
            )
        columns = item.get("columns")
        if (
            not isinstance(columns, list)
            or len(columns) < 2
            or not all(isinstance(c, str) and c.strip() for c in columns)
        ):
            raise ValueError(
                f"feature_engineer response 'interactions[{i}]' field 'columns' must be a "
                f"list of at least 2 non-empty strings, got {columns!r}"
            )
        itype = item.get("type")
        if not isinstance(itype, str) or not itype.strip():
            raise ValueError(
                f"feature_engineer response 'interactions[{i}]' missing required non-empty "
                "string field 'type'"
            )
        result.append({"columns": columns, "type": itype})
    return result


def _validate_feature_spec(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "encodings": _validate_encodings(data.get("encodings")),
        "null_handling": _validate_null_handling(data.get("null_handling")),
        "interactions": _validate_interactions(data.get("interactions")),
    }


class FeatureEngineerNode(LLMNode):
    name = "feature_engineer"

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        solution_plan = _read_solution_plan(state, workspace)
        eda_report = _read_eda_report(state, workspace)
        messages.append(
            HumanMessage(
                content=(f"## Solution plan\n\n{solution_plan}\n\n## EDA report\n\n{eda_report}")
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        validated = _validate_feature_spec(data)
        return workspace.write_json(relative_path, validated)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"feature_spec_path": written_path}
