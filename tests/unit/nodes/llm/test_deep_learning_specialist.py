"""Unit tests for src/nodes/llm/deep_learning_specialist.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both import locations, matching
`test_classical_ml_specialist.py`'s convention). No network calls, no real
filesystem writes.

The `design.json` schema rules themselves are covered exhaustively in
`test_experiment_design.py`; the cases here are the node's own contract —
output path, injected fields, prompt assembly, its own neural model-family
table, and the two neural-specific rules that live in the prompt rather than in
the validator (fit-per-fold preprocessing, scalar-only architecture params).

Every model-family case is parametrized over the **imported production table**,
never a hand-written copy: T-024's review round found that a copied table let
adversarial mutation of the real one leave the suite green.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm._experiment_design import FORBIDDEN_CV_KEYS
from src.nodes.llm.deep_learning_specialist import (
    _MODEL_FAMILIES,
    DeepLearningSpecialistNode,
)
from src.state import new_state

# The worked example from `config/prompts/deep_learning_specialist/v1.md`.
# `test_prompt_json_example_matches_this_modules_payload` pins the two together,
# so the mocked responses here stay the shape the prompt actually asks for.
VALID_DESIGN: dict[str, Any] = {
    "model_family": "mlp",
    "search_space": {
        "n_layers": {"type": "int", "low": 2, "high": 5},
        "layer_width": {"type": "int", "low": 64, "high": 512, "log": True},
        "width_decay": {"type": "float", "low": 0.4, "high": 1.0},
        "embedding_dim_multiplier": {"type": "float", "low": 0.5, "high": 2.0},
        "dropout": {"type": "float", "low": 0.0, "high": 0.5},
        "learning_rate": {"type": "float", "low": 0.0001, "high": 0.01, "log": True},
        "weight_decay": {"type": "float", "low": 0.000001, "high": 0.001, "log": True},
        "activation": {"type": "categorical", "choices": ["relu", "gelu"]},
    },
    "fixed_params": {
        "batch_size": 256,
        "max_epochs": 100,
        "patience": 10,
        "optimizer": "adamw",
    },
    "preprocessing": [
        "standard_scaler_fitted_per_fold",
        "median_imputation_fitted_per_fold",
        "categorical_embeddings",
    ],
    "rationale": (
        "The plan gives no explicit row count, so this is a deliberately modest MLP with learned "
        "categorical embeddings rather than TabNet: 2-5 layers with a decaying width, dropout and "
        "weight decay to control overfitting if the dataset turns out to be small. Scaling and "
        "imputation are fitted per fold to keep the frozen-fold scores comparable."
    ),
}
RESPONSE_CONTENT = json.dumps(VALID_DESIGN)
SOLUTION_PLAN = {"model_families": ["neural network"], "order": ["neural network"]}
FOLD_CONFIG = {
    "strategy": "stratified_kfold",
    "n_folds": 5,
    "seed": 42,
    "fold_indices": [{"train": [111111], "val": [222222]}],
}

# (alias the LLM might write, canonical family it must resolve to) for every alias
# in the real table. Deleting, shrinking or typo'ing a production alias fails a test.
_ALIAS_CASES = [(alias, family) for family, aliases in _MODEL_FAMILIES.items() for alias in aliases]


def _design(**overrides: Any) -> str:
    data = copy.deepcopy(VALID_DESIGN)
    data.update(overrides)
    return json.dumps(data)


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


def _first_json_block(prompt: str) -> dict[str, Any]:
    """Parse the first ```json fenced block of the prompt — the worked example.

    The rejected-shape snippets in the prompt are deliberately written as inline
    code spans rather than fenced blocks, precisely so this lookup finds the one
    payload that is supposed to be valid.
    """
    marker = "```json\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n```", start)
    parsed = json.loads(prompt[start:end])
    assert isinstance(parsed, dict)
    return parsed


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
    """Patched at both import locations — `src.nodes.llm.base` (the base class's
    own `__call__`, which writes the output) and
    `src.nodes.llm.deep_learning_specialist` (the node's own instance, built in
    `_build_messages` to read the upstream artifacts) — resolving to the same
    mock instance, since neither call site knows about the other's."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = SOLUTION_PLAN
    instance.write_json.return_value = "/workspace/experiments/exp_0/design.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.deep_learning_specialist.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> Any:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"
    state["validation_config_path"] = "validation/fold_config.json"
    state["feature_spec_path"] = "design/iteration_0/feature_spec.json"
    return state


def _written_design(workspace_instance: MagicMock) -> dict[str, Any]:
    args, _ = workspace_instance.write_json.call_args
    return args[1]


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("deep_learning_specialist")

    assert config.name == "deep_learning_specialist"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "experiments/exp_{iteration}/design.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("deep_learning_specialist", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — deep_learning_specialist" in prompt


# -- __call__ behavior --


def test_call_writes_design_with_search_space_and_neural_model_family(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_0/design.json"
    assert args[1]["model_family"] == "mlp"
    assert "n_layers" in args[1]["search_space"]


def test_output_path_uses_current_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state(current_iteration=3))

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_3/design.json"


def test_delta_adds_no_new_labstate_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`coder` (T-029) reads `experiments/exp_{iteration}/design.json` from its
    well-known path, so this node must not introduce a `LabState` field —
    `src/state.py` is a protected contract."""
    node = DeepLearningSpecialistNode()

    delta = node(_build_state())

    assert set(delta) == {"messages"}


# -- frozen folds (Done when: "the design references the frozen folds") --


def test_written_design_references_frozen_folds(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(cv_strategy_ref="my_own_folds.json"))
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


def test_written_design_references_frozen_folds_when_llm_omits_the_key(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_llm_cv_key_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, key: str
) -> None:
    """A design that tries to redefine cross-validation is rejected outright and
    nothing is written — the folds are frozen (CLAUDE.md invariant #1)."""
    mock_llm.invoke.return_value = AIMessage(content=_design(**{key: "whatever"}))
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_shuffle_in_fixed_params_is_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The neural-specific trap the prompt warns about: a PyTorch training config
    naturally wants a DataLoader `shuffle` flag, but `shuffle` is a forbidden CV
    key even nested inside `fixed_params`. Within-fold batch shuffling is
    `coder`'s business against the frozen folds."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(fixed_params={"batch_size": 256, "shuffle": True})
    )
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="shuffle"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


# -- injected fields --


def test_specialist_field_injected_not_taken_from_llm(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(specialist="nlp_specialist"))
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["specialist"] == "deep_learning_specialist"


def test_feature_spec_ref_relativized_from_state(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """An absolute host path baked into `design.json` breaks inside
    `code_executor`'s subprocess and inside the container, where the workspace is
    bind-mounted elsewhere."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["feature_spec_path"] = "/workspace/design/iteration_0/feature_spec.json"
    node = DeepLearningSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["feature_spec_ref"] == "design/iteration_0/feature_spec.json"


def test_feature_spec_ref_falls_back_when_unset(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    state = _build_state(current_iteration=2)
    state["feature_spec_path"] = ""
    node = DeepLearningSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["feature_spec_ref"] == "design/iteration_2/feature_spec.json"


def test_n_trials_from_llm_not_written(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The trial budget lives in `config/settings.yaml`'s `optuna:` block, never
    in a per-experiment design."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(n_trials=500, early_stopping_patience=99)
    )
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert "n_trials" not in written
    assert "early_stopping_patience" not in written


# -- model-family table integrity --


def test_model_family_table_is_exactly_the_three_neural_families() -> None:
    assert set(_MODEL_FAMILIES) == {"tabnet", "node", "mlp"}


@pytest.mark.parametrize(("alias", "expected_family"), _ALIAS_CASES)
def test_every_production_alias_resolves_to_its_family(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    alias: str,
    expected_family: str,
) -> None:
    """`coder` (T-029) dispatches on the written `model_family`, so every alias in
    the real table must canonicalize rather than pass through."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=alias))
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == expected_family


def test_spelled_out_node_with_acronym_resolves_to_one_family(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """`node`'s acronym and its spelled-out aliases belong to the same family, so
    the natural phrasing resolves instead of being flagged ambiguous."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(model_family="NODE (Neural Oblivious Decision Ensembles)")
    )
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "node"


@pytest.mark.parametrize("family", ["mlpclassifier", "tabnetv2", "nodes"])
def test_model_family_substring_without_word_boundary_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    family: str,
) -> None:
    """Matching is whole-phrase with word boundaries; swapping it for a substring
    check would silently accept all three of these."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=family))
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())


@pytest.mark.parametrize("family", ["xgboost", "lightgbm", "extra_trees"])
def test_classical_family_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    family: str,
) -> None:
    """This node carries its own table — it must not inherit
    `classical_ml_specialist`'s families."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=family))
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())


def test_ambiguous_two_family_response_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family="tabnet or mlp"))
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="ambiguous"):
        node(_build_state())


# -- prompt <-> validator agreement (the two neural-specific rules) --


def test_prompt_json_example_matches_this_modules_payload() -> None:
    """Keeps `VALID_DESIGN` — reused by every mocked response here and by the
    phase-5 integration mock — in sync with what the prompt actually asks for."""
    prompt = PromptLoader().load("deep_learning_specialist", "v1")

    assert _first_json_block(prompt) == VALID_DESIGN


def test_prompt_json_example_passes_the_real_validator(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A prompt documenting a payload the shared validator rejects would fail the
    run in production; catch it here instead."""
    prompt = PromptLoader().load("deep_learning_specialist", "v1")
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(_first_json_block(prompt)))
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["model_family"] == "mlp"
    assert "n_layers" in written["search_space"]


def test_list_valued_categorical_choices_is_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Why the prompt decomposes an architecture into scalar parameters: tuning
    over layer-width tuples is not expressible, because `choices` takes only JSON
    scalars."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(
            search_space={"hidden_dims": {"type": "categorical", "choices": [[64, 32], [128, 64]]}}
        )
    )
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="JSON scalars"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_fixed_flat_list_hidden_dims_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The asymmetry the prompt states: a *fixed* architecture is a flat list of
    scalars, which `fixed_params` accepts — only tuning over architectures is
    blocked."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(fixed_params={"hidden_dims": [256, 128]})
    )
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["fixed_params"]["hidden_dims"] == [256, 128]


def test_preprocessing_fit_per_fold_tokens_are_valid_step_names(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The prompt tells the LLM to make fit scope visible in the token itself
    (`standard_scaler_fitted_per_fold`); those tokens must satisfy the shared
    lower_snake shape constraint, or the advice would break every design."""
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["preprocessing"] == VALID_DESIGN["preprocessing"]
    assert "standard_scaler_fitted_per_fold" in written["preprocessing"]


# -- prompt assembly --


def test_build_messages_includes_plan_and_fold_summary(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = [SOLUTION_PLAN, FOLD_CONFIG]
    node = DeepLearningSpecialistNode()

    node(_build_state())

    last_message = mock_llm.invoke.call_args[0][0][-1]
    assert "## Solution plan" in last_message.content
    assert "neural network" in last_message.content
    assert "## Frozen CV folds" in last_message.content
    assert "stratified_kfold" in last_message.content
    assert "## Feature spec reference" in last_message.content
    # The per-row fold assignments are never injected — they would flood the
    # context window with data the specialist has no use for.
    assert "fold_indices" not in last_message.content
    assert "222222" not in last_message.content


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Phase 5 can be exercised standalone with no Phase 1/4 run ahead of it, so
    unset upstream paths degrade to a placeholder rather than raising."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["solution_plan_path"] = ""
    state["validation_config_path"] = ""
    node = DeepLearningSpecialistNode()

    node(state)

    workspace_instance.read_json.assert_not_called()
    assert "not yet available" in mock_llm.invoke.call_args[0][0][-1].content


def test_build_messages_handles_unreadable_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert "unable to read" in mock_llm.invoke.call_args[0][0][-1].content


def test_build_messages_handles_corrupt_upstream_json(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A truncated `solution_plan.json` raises `json.JSONDecodeError` out of
    `read_json`. Both readers in `_build_messages` must absorb it — hardening only
    the fold reader would leave the run just as dead."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = json.JSONDecodeError("truncated", "{", 0)
    node = DeepLearningSpecialistNode()

    node(_build_state())

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "unable to read solution plan" in content
    assert "unable to read frozen fold config" in content


def test_build_messages_handles_foreign_absolute_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Resuming a checkpointed run after the workspace moved leaves absolute
    paths that no longer sit under the current root — `relative_to_workspace`
    raises `ValueError`, which every reader in this node must absorb."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["solution_plan_path"] = "/old/workspace/design/iteration_0/solution_plan.json"
    state["validation_config_path"] = "/old/workspace/validation/fold_config.json"
    node = DeepLearningSpecialistNode()

    node(state)

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "unable to read solution plan" in content
    assert "unable to read frozen fold config" in content
    workspace_instance.write_json.assert_called_once()


# -- response parsing / validation failures --


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "mlp"


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="deep_learning_specialist"):
        node(_build_state())


def test_nothing_written_when_validation_fails(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(search_space={}))
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="search_space"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_write_output_before_build_messages_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`_write_output` depends on the feature-spec reference `_build_messages`
    resolves; called out of `__call__`'s order it must refuse rather than write a
    design pointing at the wrong feature spec."""
    _, workspace_instance = mock_workspace_manager
    node = DeepLearningSpecialistNode()

    with pytest.raises(ValueError, match="deep_learning_specialist"):
        node._write_output(
            workspace_instance,
            "experiments/exp_0/design.json",
            AIMessage(content=RESPONSE_CONTENT),
        )

    workspace_instance.write_json.assert_not_called()
