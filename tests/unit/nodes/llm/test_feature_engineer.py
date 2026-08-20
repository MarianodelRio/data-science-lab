"""Unit tests for src/nodes/llm/feature_engineer.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at their import location inside
`src.nodes.llm.base`, matching `test_baseline_designer.py`'s convention). No
network calls, no real filesystem writes.

The one exception is the final section, which exercises the two degrade-safe
reader helpers against a **real** `WorkspaceManager` rooted at `tmp_path`: a
mocked workspace cannot produce the real `ValueError` that
`WorkspaceManager._resolve` raises on a traversal path, which is precisely what
those tests are for. Same shape as `test_experiment_design.py`'s reader section.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.feature_engineer import (
    FeatureEngineerNode,
    _matched_fit_scope_family,
    _normalize_operation,
    _read_eda_report,
    _read_solution_plan,
)
from src.state import LabState, new_state
from src.workspace.workspace_manager import WorkspaceManager

# Sentinel for "this key is absent from the entry entirely", distinct from any
# JSON value a response could legitimately carry (including `None`).
_MISSING = object()


def _entry(**overrides: Any) -> dict[str, Any]:
    """A minimal valid v2 entry, overridable per test."""
    base: dict[str, Any] = {
        "columns": ["hour"],
        "operation": "cyclical_sin_cos",
        "params": {"period": 24},
        "fit_scope": "global",
        "rationale": "hour is cyclical; raw integers imply a false 23-to-0 discontinuity",
    }
    return {**base, **overrides}


def _entry_with(field: str, value: Any) -> dict[str, Any]:
    """A valid entry with one field overridden, or removed when `value` is `_MISSING`."""
    entry = _entry()
    if value is _MISSING:
        del entry[field]
    else:
        entry[field] = value
    return entry


VALID_SPEC = {
    "features": [
        # one column
        _entry(),
        # many columns — same shape, no separate `interactions` section in v2
        _entry(
            columns=["num1", "num2"],
            operation="ratio",
            params={},
            fit_scope="global",
            rationale="unit price separates the two target classes in the EDA",
        ),
        # a leakage-prone family with the fit scope it requires
        _entry(
            columns=["user_id"],
            operation="target_encoding",
            params={"smoothing": 10},
            fit_scope="per_fold",
            rationale="user_id has 40k levels and a predictive target mean",
        ),
    ]
}
RESPONSE_CONTENT = json.dumps(VALID_SPEC)
SOLUTION_PLAN = {
    "approach": "gradient_boosting",
    "candidate_models": ["lightgbm"],
}
EDA_REPORT_TEXT = "# EDA Report\n\nOne file found: `data/raw/train.csv`."


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=RESPONSE_CONTENT)
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def patched_settings():
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings()
        yield mock_settings_cls


@pytest.fixture
def mock_workspace_manager():
    """Patched at both import locations: `src.nodes.llm.base` (used by the
    base class's own `__call__` to write output) and
    `src.nodes.llm.feature_engineer` (the node's own instance, constructed
    in its `_build_messages` override to read the upstream artifacts) —
    both must resolve to the same mock instance since neither call site is
    aware of the other's WorkspaceManager."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = SOLUTION_PLAN
    instance.read_text.return_value = EDA_REPORT_TEXT
    instance.write_json.return_value = "/workspace/design/iteration_0/feature_spec.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.feature_engineer.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["solution_plan_path"] = "design/solution_plan.json"
    state["eda_report_path"] = "reports/eda_report.md"
    return state


def _respond(patched_llm_factory: MagicMock, data: Any) -> None:
    """Point the mocked LLM at `data`, serialized as its whole response."""
    patched_llm_factory.get.return_value.invoke.return_value = AIMessage(content=json.dumps(data))


def _written_features(workspace_instance: MagicMock) -> list[dict[str, Any]]:
    args, _ = workspace_instance.write_json.call_args
    features: list[dict[str, Any]] = args[1]["features"]
    return features


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    """T-047 is the repo's first `prompt_version` bump. `v1.md` deliberately stays
    on disk — `PromptLoader` is version-addressed and nothing enumerates the
    directory — so only the config's pointer moves."""
    config = load_agent_config("feature_engineer")

    assert config.name == "feature_engineer"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v2"
    assert config.tools == ()
    assert config.output_file_pattern == "design/iteration_{iteration}/feature_spec.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("feature_engineer", "v2")
    assert prompt.strip() != ""
    assert "# System prompt — feature_engineer" in prompt
    # Cheap guard that the prompt's vocabulary and the validator's agree: a prompt
    # that never names the field or its two legal values cannot elicit a valid spec.
    assert "fit_scope" in prompt
    assert "per_fold" in prompt
    assert "global" in prompt


# -- __call__ behavior --


def test_call_writes_v2_spec_via_workspace_write_json(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """A valid spec carrying both a 1-column and an N-column entry is written
    through unchanged — the whitelist rebuild is lossless for a conforming
    response."""
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "design/iteration_0/feature_spec.json"
    assert args[1] == VALID_SPEC


def test_unknown_entry_keys_are_dropped_by_whitelist_rebuild(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`_validate_feature_entry` returns a fresh dict with exactly the five v2 keys
    in a fixed order; the LLM's own object is never written through. v1's
    `fold_aware`/`method` are the keys most likely to reappear from an
    under-instructed model, so they are the ones asserted away here."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(fold_aware=True, method="cyclical", notes="ignore me")]},
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    written = _written_features(workspace_instance)[0]
    assert list(written.keys()) == ["columns", "operation", "params", "fit_scope", "rationale"]


def test_call_sets_feature_spec_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`feature_spec_path` is load-bearing beyond its own value:
    `analysis_critic._detect_phase_stem` (T-016) uses its presence as the
    Phase-1-vs-Phase-4 discriminator, so `_build_output_state` must keep
    returning it — v2 changes the file's *contents*, never this field."""
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    delta = node(state)

    assert delta["feature_spec_path"] == workspace_instance.write_json.return_value


def test_call_reads_solution_plan_and_eda_report(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    workspace_instance.read_json.assert_called_once_with("design/solution_plan.json")
    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")


def test_build_messages_includes_both_sections(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "## Solution plan" in last_message.content
    assert "gradient_boosting" in last_message.content
    assert "## EDA report" in last_message.content
    assert EDA_REPORT_TEXT in last_message.content


def test_relative_to_workspace_converts_absolute_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()
    state["solution_plan_path"] = "/workspace/design/solution_plan.json"
    state["eda_report_path"] = "/workspace/reports/eda_report.md"

    node(state)

    workspace_instance.read_json.assert_called_once_with("design/solution_plan.json")
    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1] == VALID_SPEC


# -- missing/unreadable upstream artifacts --


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """When run standalone with no T-021 (solution_architect) output yet, missing
    upstream `LabState` path fields must degrade to a placeholder message, not
    raise — mirrors `baseline_designer`'s handling of the same not-yet-available
    case."""
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()
    state["solution_plan_path"] = ""
    state["eda_report_path"] = ""

    node(state)

    workspace_instance.read_json.assert_not_called()
    workspace_instance.read_text.assert_not_called()
    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "not yet available" in last_message.content


def test_build_messages_handles_unreadable_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    workspace_instance.read_text.side_effect = OSError("boom")
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "unable to read" in last_message.content


def test_build_messages_handles_recursion_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """`RecursionError` is a `RuntimeError`, so neither `OSError` nor `ValueError`
    catches it — it is in `DEGRADE_ERRORS` precisely because a pathologically
    nested artifact must not abort the graph run (T-047 / the T-024 discovery)."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = RecursionError("too deep")
    node = FeatureEngineerNode()

    node(_build_state())

    invoked_messages = mock_llm.invoke.call_args[0][0]
    assert "unable to read solution plan" in invoked_messages[-1].content


# -- JSON parsing errors --


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="feature_engineer"):
        node(state)


def test_non_dict_top_level_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps([VALID_SPEC]))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="feature_engineer"):
        node(state)


def test_unclosed_fence_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="```json\n{}")
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="never closes it"):
        node(_build_state())


def test_fence_with_no_content_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="``````")
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="fence has no content"):
        node(_build_state())


# -- top-level `features` shape --


@pytest.mark.parametrize("value", [_MISSING, "x", {}, None, 5, 0])
def test_missing_or_non_list_features_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    data: dict[str, Any] = {} if value is _MISSING else {"features": value}
    _respond(patched_llm_factory, data)
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="feature_engineer.*'features'"):
        node(_build_state())


@pytest.mark.parametrize("item", ["x", 5, None, ["a"], True])
def test_feature_entry_not_an_object_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, item: Any
) -> None:
    _respond(patched_llm_factory, {"features": [item]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="feature_engineer.*must be an object"):
        node(_build_state())


def test_empty_features_list_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """A competition where the EDA justifies no transformation at all is a valid
    design, not a failure — `features` is required, its emptiness is not."""
    _respond(patched_llm_factory, {"features": []})
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    args, _ = workspace_instance.write_json.call_args
    assert args[1] == {"features": []}


def test_duplicate_columns_operation_pair_across_entries_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Two entries sharing a `(columns, operation)` pair name the same derived column.
    `coder` (T-029) would generate it twice and either collide or silently duplicate.
    The check is on the pair, **not** on full-entry equality: differing `params` change
    the values but not the name, so that case is a collision too."""
    _respond(
        patched_llm_factory,
        {
            "features": [
                _entry(columns=["user_id"], operation="target_encoding", fit_scope="per_fold"),
                _entry(
                    columns=["user_id"],
                    operation="target_encoding",
                    params={"smoothing": 99},
                    fit_scope="per_fold",
                    rationale="a different smoothing, but the same derived column name",
                ),
            ]
        },
    )
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="features\\[1\\]"):
        node(_build_state())


def test_same_operation_on_different_columns_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The duplicate check keys on the *pair*. One operation applied to several
    different columns is the normal case and must stay legal."""
    _respond(
        patched_llm_factory,
        {
            "features": [
                _entry(columns=["user_id"], operation="target_encoding", fit_scope="per_fold"),
                _entry(columns=["item_id"], operation="target_encoding", fit_scope="per_fold"),
                _entry(columns=["user_id"], operation="frequency_encoding", fit_scope="per_fold"),
            ]
        },
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert len(_written_features(workspace_instance)) == 3


def test_column_order_distinguishes_two_entries(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`columns` is an ordered list and `coder` reads it positionally — `a/b` and `b/a`
    are different ratios, so the pair key is the tuple, not a set."""
    _respond(
        patched_llm_factory,
        {
            "features": [
                _entry(columns=["a", "b"], operation="ratio"),
                _entry(columns=["b", "a"], operation="ratio"),
            ]
        },
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert len(_written_features(workspace_instance)) == 2


def test_operation_and_rationale_are_written_stripped(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`"  target_encoding  "` passes the `.strip()` truthiness check, so the padding
    used to reach the artifact verbatim. `coder` string-matches `operation`, and the
    family guard matches on word boundaries, so both fields are stripped on the way in."""
    _respond(
        patched_llm_factory,
        {
            "features": [
                _entry(
                    operation="  cyclical_sin_cos\n",
                    rationale="\t hour is cyclical  ",
                )
            ]
        },
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    written = _written_features(workspace_instance)[0]
    assert written["operation"] == "cyclical_sin_cos"
    assert written["rationale"] == "hour is cyclical"


def test_padded_family_operation_is_still_flagged(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Stripping happens before the family guard sees the operation, so padding cannot
    be used — accidentally or otherwise — to slip a fitted operation past it."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation="  target_encoding  ", params={}, fit_scope="global")]},
    )
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="target encoding"):
        node(_build_state())


# -- per-entry field validation --


@pytest.mark.parametrize(
    "value", [_MISSING, [], "hour", ["a", ""], ["a", "   "], ["a", 3], None, {}, ["a", None]]
)
def test_invalid_columns_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    _respond(patched_llm_factory, {"features": [_entry_with("columns", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="columns"):
        node(_build_state())


@pytest.mark.parametrize("value", [["amount", "amount"], ["a", "b", "a"], ["hour", "hour", "hour"]])
def test_duplicate_columns_within_one_entry_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    """`{"columns": ["amount", "amount"], "operation": "ratio"}` is a constant-1
    feature. It validated under the first T-047 implementation and would have reached
    `coder` (T-029), where a column of ones reads as a modeling problem rather than as
    the specification bug it is. `solution_architect` sets the precedent by rejecting
    normalized-duplicate `model_families`."""
    _respond(patched_llm_factory, {"features": [_entry_with("columns", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="columns"):
        node(_build_state())


@pytest.mark.parametrize("value", [_MISSING, "", "   ", 5, None, ["x"], True])
def test_invalid_operation_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    _respond(patched_llm_factory, {"features": [_entry_with("operation", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="operation"):
        node(_build_state())


@pytest.mark.parametrize("value", [_MISSING, "", "   ", 5, None, ["x"], True])
def test_invalid_rationale_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    _respond(patched_llm_factory, {"features": [_entry_with("rationale", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="rationale"):
        node(_build_state())


@pytest.mark.parametrize(
    "value", [_MISSING, "", "per-fold", "PER_FOLD", "Global", "train", True, False, None, 0]
)
def test_invalid_fit_scope_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    """`fit_scope` is required on every entry and has no default. Matching is by
    exact token and is deliberately neither case-folded nor separator-normalized
    (unlike *operation* matching): `coder` branches on this value, so only the two
    canonical spellings may reach the artifact. `True`/`False` are rejected by the
    same membership test — no `isinstance(bool)` special case is needed, because
    neither compares equal to either token."""
    _respond(patched_llm_factory, {"features": [_entry_with("fit_scope", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="fit_scope"):
        node(_build_state())


# -- `params` --


@pytest.mark.parametrize("value", [_MISSING, "none", [], None, 5, True])
def test_params_not_an_object_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    _respond(patched_llm_factory, {"features": [_entry_with("params", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="params"):
        node(_build_state())


@pytest.mark.parametrize(
    "value",
    [
        {"a": {"b": 1}},
        {"a": [[1]]},
        {"a": [{"b": 1}]},
        {"a": [1, {"b": 2}]},
        {"a": float("nan")},
        {"a": float("inf")},
        {"a": float("-inf")},
        {"a": 2**53 + 1},
        {"a": -(2**53) - 1},
        {"a": [2**53 + 1]},
    ],
)
def test_invalid_params_value_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    """`is_json_scalar` (shared with `_experiment_design`'s `fixed_params`/`choices`)
    rejects what `WorkspaceManager.write_json` would otherwise write happily but no
    non-Python consumer reads back correctly: `NaN`/`Infinity` are not RFC-8259, and
    an int beyond ±2**53 is rounded by every IEEE-754 double consumer. A nested
    object is rejected for a different reason — it cannot be applied to a column."""
    _respond(patched_llm_factory, {"features": [_entry_with("params", value)]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="params"):
        node(_build_state())


def test_params_empty_key_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Only reachable with an empty key: `json.loads` always produces `str` keys, so
    `{1: 2}` arrives as `{"1": 2}` and is legitimately accepted."""
    _respond(patched_llm_factory, {"features": [_entry_with("params", {"": 1})]})
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="params"):
        node(_build_state())


def test_empty_params_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _respond(patched_llm_factory, {"features": [_entry_with("params", {})]})
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert _written_features(workspace_instance)[0]["params"] == {}


@pytest.mark.parametrize(
    "value",
    [
        {"bins": [0, 10, 20]},
        {"aggs": ["mean", "std"]},
        {"quantiles": [0.25, 0.75]},
        {"flags": [True, None]},
        {"empty": []},
        {"mixed": [1, "a", True, None], "scalar": 3},
    ],
)
def test_params_flat_list_of_scalars_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, value: Any
) -> None:
    """A flat list of scalars is correct input, not a violation — `{"bins": [0, 10,
    20]}` is the natural way to parameterize binning. This is the exact predicate
    `_experiment_design._validate_fixed_params` uses, reused rather than re-derived."""
    _respond(patched_llm_factory, {"features": [_entry_with("params", value)]})
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert _written_features(workspace_instance)[0]["params"] == value


# -- fit-scope family matching --

# One list, both directions, and it carries the **expected family label** rather than the
# operation alone. `match="per_fold"` alone was not enough: every family's rejection message
# contains that string, so a keyword accidentally relocated from one tuple to another would have
# passed every case. Asserting the label pins which family matched, the way
# `test_frequency_encoding_excluding_target_leak_is_now_flagged` already did. A keyword added to
# or dropped from `_FIT_SCOPE_SENSITIVE_FAMILIES` still moves the "must be per_fold" and the
# "per_fold is accepted" tests together, because both parametrize over this one list.
#
# Case, `-`/`_` separator and camelCase variants are included on purpose — matching normalizes
# all three before the whole-phrase check, and sklearn's own class names (`TargetEncoder`,
# `MinMaxScaler`, `KNNImputer`) are probable `operation` values.
_TARGET_ENCODING = "target encoding"
_IMPUTATION = "statistical imputation"
_SCALING = "scaling / normalization"
_BINNING = "binning / discretization"
_DIM_REDUCTION = "dimensionality reduction"
_FREQUENCY = "frequency / count encoding"

_FAMILY_OPERATIONS = [
    # target encoding (T-022's curated tuple; `target_encode`/`target_encoder` added in the
    # T-047 review round — the noun form matched and the verb/agent forms did not)
    ("target_encoding", _TARGET_ENCODING),
    ("target-encoding", _TARGET_ENCODING),
    ("Target Encoding", _TARGET_ENCODING),
    ("target_encode", _TARGET_ENCODING),
    ("target_encoder", _TARGET_ENCODING),
    ("TargetEncoder", _TARGET_ENCODING),
    ("mean_encoding", _TARGET_ENCODING),
    ("leave_one_out", _TARGET_ENCODING),
    ("leave-one-out", _TARGET_ENCODING),
    ("WOE", _TARGET_ENCODING),
    ("weight of evidence", _TARGET_ENCODING),
    ("CatBoost encoding", _TARGET_ENCODING),
    ("CatBoostEncoder", _TARGET_ENCODING),
    ("cat_boost_encoder", _TARGET_ENCODING),
    ("James-Stein encoding", _TARGET_ENCODING),
    ("M-estimate encoding", _TARGET_ENCODING),
    ("impact_encoding", _TARGET_ENCODING),
    ("target_mean", _TARGET_ENCODING),
    ("smoothed target encoding", _TARGET_ENCODING),
    # statistical imputation
    ("median_impute", _IMPUTATION),
    ("median-imputation", _IMPUTATION),
    ("Mean Imputer", _IMPUTATION),
    ("mode_impute", _IMPUTATION),
    ("most_frequent_imputer", _IMPUTATION),
    ("MostFrequentImputer", _IMPUTATION),
    ("knn_impute", _IMPUTATION),
    ("iterative_imputation", _IMPUTATION),
    ("mice", _IMPUTATION),
    ("simple_imputer", _IMPUTATION),
    ("simple_impute", _IMPUTATION),
    ("SimpleImputer", _IMPUTATION),
    ("KNNImputer", _IMPUTATION),
    ("impute_median", _IMPUTATION),
    ("impute_mean", _IMPUTATION),
    ("impute_mode", _IMPUTATION),
    ("fillna_median", _IMPUTATION),
    ("fillna_mean", _IMPUTATION),
    ("fillna_mode", _IMPUTATION),
    ("median_fill", _IMPUTATION),
    ("mean_fill", _IMPUTATION),
    ("mode_fill", _IMPUTATION),
    # scalers / normalizers
    ("standard_scale", _SCALING),
    ("standard-scale", _SCALING),
    ("Standard Scale", _SCALING),
    ("StandardScaler", _SCALING),
    ("min_max_scaler", _SCALING),
    ("MinMaxScaler", _SCALING),
    ("robust_scaling", _SCALING),
    ("z_score", _SCALING),
    ("zscore_normalization", _SCALING),
    ("maxabs_scale", _SCALING),
    ("max_abs_scaling", _SCALING),
    ("MaxAbsScaler", _SCALING),
    ("quantile_transform", _SCALING),
    ("power_transform", _SCALING),
    ("yeo_johnson", _SCALING),
    ("box-cox", _SCALING),
    # binning / discretization
    ("quantile_bin", _BINNING),
    ("kbins", _BINNING),
    ("kbins_discretizer", _BINNING),
    ("KBinsDiscretizer", _BINNING),
    ("equal_frequency_binning", _BINNING),
    ("equal_width_binning", _BINNING),
    ("discretize", _BINNING),
    # dimensionality reduction
    ("pca", _DIM_REDUCTION),
    ("PCA", _DIM_REDUCTION),
    ("truncated_svd", _DIM_REDUCTION),
    ("TruncatedSVD", _DIM_REDUCTION),
    ("umap", _DIM_REDUCTION),
    ("tsne", _DIM_REDUCTION),
    ("t-sne", _DIM_REDUCTION),
    ("nmf", _DIM_REDUCTION),
    # frequency / count encoding
    ("frequency_encoding", _FREQUENCY),
    ("frequency-encoding", _FREQUENCY),
    ("count_encoding", _FREQUENCY),
    ("Count Encode", _FREQUENCY),
]


@pytest.mark.parametrize(("operation", "expected_family"), _FAMILY_OPERATIONS)
def test_leakage_prone_family_with_global_fit_scope_raises(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    operation: str,
    expected_family: str,
) -> None:
    """Every one of the six families is fitted on data, so declaring `global` for
    any of them is rejected. Target encoding leaks the target outright; the other
    five leak held-out-fold feature statistics — a milder inflation, still rejected,
    because forcing `per_fold` on something that turns out to be stateless produces
    identical output while missing a fitted operation is a silent leak.

    The message must name the family that matched, not merely `per_fold`: every
    family's message says `per_fold`, so matching only on that would let a keyword
    moved into the wrong tuple pass all of these cases."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    node = FeatureEngineerNode()

    with pytest.raises(ValueError) as excinfo:
        node(_build_state())

    message = str(excinfo.value)
    assert expected_family in message
    assert "per_fold" in message


@pytest.mark.parametrize(("operation", "expected_family"), _FAMILY_OPERATIONS)
def test_leakage_prone_family_with_per_fold_is_accepted(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    operation: str,
    expected_family: str,
) -> None:
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="per_fold")]},
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    written = _written_features(workspace_instance)[0]
    assert written["operation"] == operation
    assert written["fit_scope"] == "per_fold"


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        # ordinary camelCase word boundary
        ("TargetEncoder", "target encoder"),
        ("StandardScaler", "standard scaler"),
        ("MinMaxScaler", "min max scaler"),
        # an uppercase run followed by a capitalized word splits after the acronym,
        # not between its letters
        ("KNNImputer", "knn imputer"),
        ("TruncatedSVD", "truncated svd"),
        # a bare all-caps token has no boundary of either kind and is left whole
        ("PCA", "pca"),
        ("WOE", "woe"),
        # separators and case, unchanged from T-022
        ("standard-scale", "standard scale"),
        ("standard_scale", "standard scale"),
        ("Standard  Scale", "standard scale"),
        # documented non-coverage: an unseparated all-lowercase run cannot be split,
        # and a digit run is treated as part of the preceding word
        ("targetencoder", "targetencoder"),
        ("Top10Encoder", "top10 encoder"),
    ],
)
def test_normalize_operation_table(operation: str, expected: str) -> None:
    """Pins the normalization the keyword floor depends on. Added in the T-047 review
    round: without the camelCase split, sklearn's own class names — `TargetEncoder`,
    `StandardScaler`, `MinMaxScaler`, `KNNImputer`, `SimpleImputer`, all highly probable
    `operation` values — were reachable by no keyword in any tuple."""
    assert _normalize_operation(operation) == expected


def test_camel_case_split_does_not_lose_concatenated_keywords() -> None:
    """The reason `_matched_fit_scope_family` matches against *both* normalization
    variants. `CatBoost` is carried concatenated in the tuple (`catboost`), so splitting
    it to `cat boost` would silently drop a pre-existing match — a regression traded for
    the class-name shapes rather than added on top of them."""
    assert _normalize_operation("CatBoost encoding") == "cat boost encoding"
    assert _matched_fit_scope_family("CatBoost encoding") == _TARGET_ENCODING


@pytest.mark.parametrize("operation", ["l2_normalize", "unit_norm", "L2Normalizer", "Normalizer"])
def test_row_wise_normalizer_with_global_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, operation: str
) -> None:
    """Regression for the false positive the T-047 review found executed. sklearn's
    `Normalizer` scales each sample by its own norm and learns nothing from any other
    row: it is stateless, so `global` is the *correct* declaration the prompt asks for.
    Flagging it made a conforming response raise, and `LLMNode.__call__` does not catch
    `ValueError`, so a correct spec terminated the Phase 4 run."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert _written_features(workspace_instance)[0]["fit_scope"] == "global"


@pytest.mark.parametrize(
    ("operation", "expected_family"),
    [
        # A2 — the concatenated scaler spellings. The tuple already carried `minmax` but not
        # `maxabs`, and `z score` but not `zscore`. (`standardize`/`standardization` were added
        # here too and removed again in round 2 — see
        # `test_standardize_prefixed_stateless_operation_with_global_is_accepted`.)
        ("maxabs_scale", _SCALING),
        ("zscore_normalization", _SCALING),
        # A3 — verb-first and pandas phrasings. `fillna_median` is the most probable name of all.
        ("fillna_median", _IMPUTATION),
        ("impute_median", _IMPUTATION),
        ("simple_impute", _IMPUTATION),
        # A4 — the verb and agent forms of the highest-severity family. Only the noun
        # (`target_encoding`) matched; `TargetEncoder` needed the camelCase split as well.
        ("target_encode", _TARGET_ENCODING),
        ("target_encoder", _TARGET_ENCODING),
        ("TargetEncoder", _TARGET_ENCODING),
        # Round 2 — `CatBoostEncoder` is `category_encoders`' real class name and the camelCase
        # split reaches it only via the separated `cat boost` keyword; `most_frequent_imputer` is
        # the third form `config/prompts/feature_engineer/v2.md` promised and the tuple lacked.
        ("CatBoostEncoder", _TARGET_ENCODING),
        ("cat_boost_encoder", _TARGET_ENCODING),
        ("most_frequent_imputer", _IMPUTATION),
        ("MostFrequentImputer", _IMPUTATION),
    ],
)
def test_review_round_keyword_additions_are_flagged(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    operation: str,
    expected_family: str,
) -> None:
    """Named regression for the four keyword gaps the T-047 review found by executing
    `_matched_fit_scope_family`. Each of these returned `None` and was therefore written
    with `fit_scope: "global"`, silently leaking held-out-fold statistics — or, for the
    target-encoding forms, the target itself. They are also in `_FAMILY_OPERATIONS`; this
    test exists so the gaps stay greppable by the finding that produced them."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    node = FeatureEngineerNode()

    with pytest.raises(ValueError) as excinfo:
        node(_build_state())

    assert expected_family in str(excinfo.value)


@pytest.mark.parametrize(
    "operation",
    [
        "standardize_address_format",
        "standardize_text_case",
        "standardize_country_codes",
        "standardize_units_to_metric",
        "StandardizePhoneNumber",
        "StandardizeTextCase",
    ],
)
def test_standardize_prefixed_stateless_operation_with_global_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, operation: str
) -> None:
    """Regression for the false positive round 1 of the T-047 review introduced and round 2
    removed. `standardize`/`standardization` were added to `_SCALER_KEYWORDS` as "the
    plain-English verb for z-scoring", but the word means "make uniform" at least as often, so
    every one of these stateless operations matched and raised.

    That is not a harmlessly-conservative outcome: the guard raises `ValueError` rather than
    coercing `fit_scope`, and `LLMNode.__call__` does not catch it — a false positive aborts the
    Phase 4 run on a correct response. Genuine z-scoring stays covered by `standard_scale`,
    `z_score` and `zscore`, pinned in `_FAMILY_OPERATIONS`."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert _written_features(workspace_instance)[0]["fit_scope"] == "global"


@pytest.mark.parametrize(
    "operation",
    [
        "log_transform",
        "standard_deviation_ratio",
        "mean_of_last_3_orders",
        "count_distinct_categories",
        "target_lag_1",
        "datetime_part_extraction",
        "text_length",
        # camelCase twins: the split normalization must not turn any of these into a match
        "LogTransform",
        "StandardDeviationRatio",
        "MeanOfLast3Orders",
        "CountDistinctCategories",
        "TargetLag1",
        "DatetimePartExtraction",
        "TextLength",
    ],
)
def test_operation_mentioning_a_family_word_is_not_flagged(
    patched_llm_factory, patched_settings, mock_workspace_manager, operation: str
) -> None:
    """False-positive regression, inherited from T-022 and generalized: matching is
    whole-phrase, so an operation that merely contains `transform`, `standard`,
    `mean`, `count` or `target` is not a family member. This is why no bare stem is
    ever added to the keyword tuples."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert _written_features(workspace_instance)[0]["operation"] == operation


def test_frequency_encoding_excluding_target_leak_is_now_flagged(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Deliberate behavior change from T-022, whose canonical false positive this
    string was. It is still **not** matched as target encoding — the property T-022
    protected holds — but v2 added a frequency/count-encoding family that it
    legitimately belongs to, and frequency encoding learns global category counts,
    so it is fitted and must be `per_fold`."""
    operation = "frequency_encoding_excluding_target_leak"
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    node = FeatureEngineerNode()

    with pytest.raises(ValueError, match="frequency / count encoding"):
        node(_build_state())


@pytest.mark.parametrize(
    "operation",
    [
        "groupby_user_mean_amount",
        "winsorize_clip_outliers",
        "quantile_clip_outliers",
        "rolling_ratio_v3",
        # camelCase twins, for the same reason as the false-positive list above
        "GroupbyUserMeanAmount",
        "WinsorizeClipOutliers",
        "QuantileClipOutliers",
        "RollingRatioV3",
    ],
)
def test_unrecognized_operation_with_global_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, operation: str
) -> None:
    """Asserts the **documented residual risk**, not a safety property. `operation`
    is an open vocabulary, so a fitted technique the keyword floor does not name —
    `groupby_user_mean_amount` aggregates over other rows and really should be
    `per_fold` — is accepted with `global`. Same honest scope as
    `_experiment_design.FORBIDDEN_CV_KEYS`: `config/prompts/feature_engineer/v2.md`'s
    general "anything fitted is per_fold" rule is what governs above the floor, and
    `code_critic`'s leakage rubric is the downstream net."""
    _respond(
        patched_llm_factory,
        {"features": [_entry(operation=operation, params={}, fit_scope="global")]},
    )
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()

    node(_build_state())

    assert _written_features(workspace_instance)[0]["fit_scope"] == "global"


# -- degrade-safe readers (real WorkspaceManager, real tmp_path files) --


def _workspace_state(tmp_path: Path) -> tuple[WorkspaceManager, LabState]:
    workspace = WorkspaceManager(str(tmp_path))
    state = new_state("comp", str(tmp_path))
    return workspace, state


def test_read_solution_plan_degrades_on_invalid_json(tmp_path: Path) -> None:
    """A truncated artifact raises `json.JSONDecodeError` — a `ValueError`, which the
    pre-T-047 bare `except OSError` did not catch, aborting the whole graph run out of
    a helper whose docstring promises it never raises."""
    workspace, state = _workspace_state(tmp_path)
    workspace.write_text("design/iteration_0/solution_plan.json", "{not json")
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"

    assert _read_solution_plan(state, workspace).startswith("(unable to read solution plan")


def test_read_solution_plan_degrades_on_traversal_path(tmp_path: Path) -> None:
    """`WorkspaceManager._resolve` raises `ValueError` on a path escaping the
    workspace — reachable from a resumed run whose recorded paths predate a move."""
    workspace, state = _workspace_state(tmp_path)
    state["solution_plan_path"] = "../../etc/passwd"

    assert _read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_pathological_nesting(tmp_path: Path) -> None:
    """A deeply nested payload exhausts the interpreter stack inside `json.loads` —
    and would again inside `json.dumps` on the way out, which is why the
    serialization is inside the `try` rather than after it. That second half is not
    independently testable: any payload deep enough to break `dumps` breaks
    `json.load` first, so this test covers the guarded block as a unit."""
    workspace, state = _workspace_state(tmp_path)
    depth = 100_000
    workspace.write_text("design/iteration_0/solution_plan.json", "[" * depth + "]" * depth)
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"

    assert _read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_non_string_path(tmp_path: Path) -> None:
    """`LabState` types this as `str`, but LangGraph does not enforce the TypedDict at
    runtime — a non-string would raise `TypeError` out of `Path()`, deliberately not in
    the caught set, so it is guarded explicitly instead."""
    workspace, state = _workspace_state(tmp_path)
    state["solution_plan_path"] = 42  # type: ignore[typeddict-item]

    assert _read_solution_plan(state, workspace) == "(solution plan not yet available)"


def test_read_eda_report_degrades_on_non_utf8_bytes(tmp_path: Path) -> None:
    """`UnicodeDecodeError` is a `ValueError`, so the pre-T-047 `except OSError` let
    it escape."""
    workspace, state = _workspace_state(tmp_path)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "eda_report.md").write_bytes(b"\xff\xfe")
    state["eda_report_path"] = "reports/eda_report.md"

    assert _read_eda_report(state, workspace).startswith("(unable to read EDA report")


def test_read_eda_report_degrades_on_traversal_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["eda_report_path"] = "../../etc/passwd"

    assert _read_eda_report(state, workspace).startswith("(unable to read")


def test_read_eda_report_degrades_on_non_string_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["eda_report_path"] = ["x"]  # type: ignore[typeddict-item]

    assert _read_eda_report(state, workspace) == "(EDA report not yet available)"
