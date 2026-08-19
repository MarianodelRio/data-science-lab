"""Unit tests for src/nodes/compute/kaggle_client.py.

Runs against a **real** `WorkspaceManager` over `tmp_path` with seeded fixtures,
matching `tests/unit/nodes/compute/test_score_evaluator.py`'s convention.

**No test here performs network I/O**, and two of them are the explicit gates on
that: `test_no_submission_file_records_not_submitted_and_never_calls_the_api`
asserts the injected fake API recorded zero calls, and
`test_missing_kaggle_credentials_records_a_reason` deletes both credential env
vars — `src.tools.kaggle_client._default_api` runs `_require_env` *before* it
imports `kaggle`, so that path never reaches the network either. Every other
test injects a fake API. `test_module_does_not_import_llm_or_langchain` is the
CLAUDE.md invariant #8 gate.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from langgraph.graph.message import add_messages

import src.nodes.compute.base as compute_base_module
import src.nodes.compute.kaggle_client as kaggle_client_module
from src.graph.node_resolver import resolve_node
from src.nodes.compute.kaggle_client import KaggleClientNode
from src.state import new_state
from src.workspace.workspace_manager import WorkspaceManager

ARTIFACT_PATH = "reports/kaggle_submission.json"

_ARTIFACT_KEYS = {
    "competition",
    "submission_file",
    "submitted",
    "lb_score",
    "cv_score",
    "cv_direction",
    "divergence",
    "divergence_flag",
    "reason",
}


class _FakeSubmission:
    def __init__(self, date: Any, public_score: Any) -> None:
        self.date = date
        self.public_score = public_score


class _FakeApi:
    """Records every call. `competition_submit`/`competition_submissions` can be
    made to raise, and `submissions` configures what `get_score` sees."""

    def __init__(
        self,
        *,
        submissions: list[Any] | None = None,
        submit_error: Exception | None = None,
        submissions_error: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.submissions = (
            submissions
            if submissions is not None
            else [_FakeSubmission(datetime(2026, 8, 19, 12, 0, 0), 0.85)]
        )
        self.submit_error = submit_error
        self.submissions_error = submissions_error

    def authenticate(self) -> None:
        self.calls.append("authenticate")

    def competition_submit(
        self, file_name: str, message: str, competition: str, quiet: bool = False
    ) -> None:
        self.calls.append("competition_submit")
        self.submitted_file = file_name
        self.submitted_message = message
        if self.submit_error is not None:
            raise self.submit_error

    def competition_submissions(self, competition: str) -> list[Any]:
        self.calls.append("competition_submissions")
        if self.submissions_error is not None:
            raise self.submissions_error
        return self.submissions


@pytest.fixture
def workspace(tmp_path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture(autouse=True)
def _no_kaggle_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt and braces: with both variables deleted, any code path that reached
    `_default_api` would raise `RuntimeError` from `_require_env` *before*
    importing `kaggle`, so no test in this module can authenticate or reach the
    network even if an assertion is later weakened."""
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)


def _state(workspace: WorkspaceManager, **overrides: Any) -> Any:
    state = new_state("titanic", str(workspace.workspace_path))
    for key, value in overrides.items():
        state[key] = value  # type: ignore[literal-required]
    return state


def _seed_submission(workspace: WorkspaceManager, directory: str) -> None:
    path = workspace.workspace_path / "experiments" / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")


def _seed_score_evaluation(
    workspace: WorkspaceManager, iteration: int, direction: str = "maximize"
) -> None:
    workspace.write_json(
        f"reports/score_evaluation_{iteration}.json",
        {"iteration": iteration, "direction": direction, "evaluated": True},
    )


def _artifact(workspace: WorkspaceManager) -> dict[str, Any]:
    return workspace.read_json(ARTIFACT_PATH)


# -- discovery / invariants -----------------------------------------------


def test_zero_arg_construction_succeeds() -> None:
    assert KaggleClientNode().name == "kaggle_client"


def test_resolve_node_returns_the_compute_node() -> None:
    """Pins both the llm -> compute fall-through and the "exactly one class
    defined in the module" convention (the imported `kaggle_tool` module must
    not put a second candidate class in this namespace)."""
    assert isinstance(resolve_node("kaggle_client"), KaggleClientNode)


def test_module_does_not_import_llm_or_langchain() -> None:
    """CLAUDE.md invariant #8 — mirrors `tests/unit/nodes/compute/test_base.py`."""
    source_path = inspect.getfile(kaggle_client_module)
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    # `src.nodes.llm` is in the set for the same reason the four newer compute
    # guards carry it (`test_score_evaluator.py`, `test_specialist_selector.py`,
    # `test_evaluation_common.py`, `test_feature_importance_extractor.py`): this
    # node's LLM-side twins (`_delivery_common`, `_evaluation_llm_common`) are
    # the imports actually within reach here, and they pull `langchain_core` in
    # transitively through `src.nodes.llm.base`.
    forbidden = ("src.llm", "src.nodes.llm", "langchain")
    assert [
        name
        for name in imported
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ] == []


# -- the network-safety gates ---------------------------------------------


def test_no_submission_file_records_not_submitted_and_never_calls_the_api(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi()
    _seed_score_evaluation(workspace, 0)

    delta = KaggleClientNode(api=api)(_state(workspace, current_iteration=1))

    artifact = _artifact(workspace)
    assert api.calls == []
    assert artifact["submitted"] is False
    assert artifact["submission_file"] == "experiments/exp_0/submission.csv"
    assert "no submission file found" in artifact["reason"]
    assert set(delta) == {"messages"}


def test_missing_kaggle_credentials_records_a_reason(workspace: WorkspaceManager) -> None:
    """No injected api and no credentials: `_default_api`'s `RuntimeError` fires
    before `import kaggle`, so this stays entirely offline."""
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode()(_state(workspace, current_iteration=1))

    artifact = _artifact(workspace)
    assert artifact["submitted"] is False
    assert "KAGGLE_USERNAME" in artifact["reason"]


# -- happy path -----------------------------------------------------------


def test_submits_and_records_lb_score_and_divergence(workspace: WorkspaceManager) -> None:
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), 0.85)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "maximize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.90))

    artifact = _artifact(workspace)
    assert artifact["submitted"] is True
    assert artifact["lb_score"] == pytest.approx(0.85)
    assert artifact["cv_score"] == pytest.approx(0.90)
    assert artifact["divergence"] == pytest.approx(0.05)
    assert artifact["reason"] is None
    assert api.calls.count("competition_submit") == 1
    assert api.calls.count("competition_submissions") == 1


def test_artifact_has_exactly_the_pinned_keys(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    assert set(_artifact(workspace)) == _ARTIFACT_KEYS


def test_delta_carries_only_a_messages_summary(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    delta = KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    assert set(delta) == {"messages"}
    assert len(delta["messages"]) == 1
    entry = delta["messages"][0]
    assert isinstance(entry, dict)
    assert entry["role"] == "assistant"
    assert "titanic" in entry["content"]
    assert "0.85" in entry["content"]


def test_message_dict_is_coerced_by_add_messages(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    delta = KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    coerced = add_messages([], delta["messages"])
    assert len(coerced) == 1
    assert coerced[0].content == delta["messages"][0]["content"]


def test_no_labstate_score_or_checkpoint_field_is_written(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    delta = KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    for field in (
        "best_score",
        "last_score",
        "best_experiment_path",
        "checkpoint_summary",
        "experiments",
    ):
        assert field not in delta


# -- divergence -----------------------------------------------------------


def test_divergence_denormalizes_a_maximize_direction(workspace: WorkspaceManager) -> None:
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), 0.8)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "maximize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["cv_score"] == pytest.approx(0.9)
    assert artifact["divergence"] == pytest.approx(0.1)
    assert artifact["divergence_flag"] is True


def test_divergence_denormalizes_a_minimize_direction(workspace: WorkspaceManager) -> None:
    """`score_evaluator` stores a minimize metric sign-flipped, so `best_score`
    is `-0.30` for an RMSE of `0.30`. Dropping the de-normalization makes
    `cv_score` `-0.30` and `divergence` `0.75` — this is the test that catches it."""
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), 0.45)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "minimize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=-0.30))

    artifact = _artifact(workspace)
    assert artifact["cv_score"] == pytest.approx(0.30)
    assert artifact["divergence"] == pytest.approx(0.15)
    assert artifact["divergence_flag"] is True


def test_divergence_flag_is_false_exactly_at_the_threshold(workspace: WorkspaceManager) -> None:
    """`0.05` and `0.0` are used deliberately: `0.90 - 0.85` is
    `0.050000000000000044` in IEEE-754 and would flag."""
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), 0.0)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "maximize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.05))

    artifact = _artifact(workspace)
    assert artifact["divergence"] == 0.05
    assert artifact["divergence_flag"] is False


def test_divergence_flag_is_true_just_past_the_threshold(workspace: WorkspaceManager) -> None:
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), 0.0)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "maximize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.06))

    assert _artifact(workspace)["divergence_flag"] is True


def test_divergence_null_when_score_evaluation_is_missing(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["cv_score"] is None
    assert artifact["cv_direction"] is None
    assert artifact["divergence"] is None
    assert artifact["divergence_flag"] is False
    assert "reports/score_evaluation_0.json" in artifact["reason"]


def test_divergence_null_when_the_direction_is_unknown(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "sideways")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["cv_score"] is None
    assert artifact["cv_direction"] is None
    assert artifact["divergence"] is None
    assert "'sideways'" in artifact["reason"]


def test_divergence_null_when_score_evaluation_json_is_malformed(
    workspace: WorkspaceManager,
) -> None:
    (workspace.workspace_path / "reports").mkdir(parents=True, exist_ok=True)
    (workspace.workspace_path / "reports" / "score_evaluation_0.json").write_text(
        "not json at all {", encoding="utf-8"
    )
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    assert _artifact(workspace)["divergence"] is None


def test_divergence_null_when_best_score_is_the_negative_infinity_sentinel(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "maximize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1))

    artifact = _artifact(workspace)
    assert artifact["cv_score"] is None
    assert artifact["cv_direction"] == "maximize"
    assert "-inf" in artifact["reason"]
    raw = (workspace.workspace_path / ARTIFACT_PATH).read_text(encoding="utf-8")
    assert "Infinity" not in raw
    assert "NaN" not in raw


def test_reads_the_score_evaluation_of_the_previous_iteration(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), 0.45)])
    _seed_submission(workspace, "exp_1")
    _seed_score_evaluation(workspace, 1, "maximize")
    _seed_score_evaluation(workspace, 2, "minimize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=2, best_score=0.5))

    assert _artifact(workspace)["cv_direction"] == "maximize"


# -- submission-file resolution -------------------------------------------


def test_submission_file_is_resolved_from_best_experiment_path(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_7")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(
        _state(
            workspace,
            current_iteration=1,
            best_score=0.9,
            best_experiment_path="experiments/exp_7",
        )
    )

    assert _artifact(workspace)["submission_file"] == "experiments/exp_7/submission.csv"


def test_submission_file_falls_back_to_the_previous_iteration_directory(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(
        _state(workspace, current_iteration=1, best_score=0.9, best_experiment_path="")
    )

    assert _artifact(workspace)["submission_file"] == "experiments/exp_0/submission.csv"


def test_absolute_best_experiment_path_is_relativized(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_3")
    _seed_score_evaluation(workspace, 0)
    absolute = str(workspace.workspace_path / "experiments" / "exp_3")

    KaggleClientNode(api=api)(
        _state(workspace, current_iteration=1, best_score=0.9, best_experiment_path=absolute)
    )

    assert _artifact(workspace)["submission_file"] == "experiments/exp_3/submission.csv"


def test_traversing_best_experiment_path_falls_back(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(
        _state(workspace, current_iteration=1, best_score=0.9, best_experiment_path="../../etc")
    )

    assert _artifact(workspace)["submission_file"] == "experiments/exp_0/submission.csv"


# -- failure paths --------------------------------------------------------


def test_invalid_competition_slug_records_a_reason_and_does_not_raise(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(
        _state(workspace, current_iteration=1, best_score=0.9, competition_name="../evil")
    )

    artifact = _artifact(workspace)
    assert artifact["submitted"] is False
    assert "'../evil'" in artifact["reason"]
    assert api.calls == []


def test_blank_competition_name_records_a_reason(workspace: WorkspaceManager) -> None:
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")

    KaggleClientNode(api=api)(
        _state(workspace, current_iteration=1, best_score=0.9, competition_name="")
    )

    artifact = _artifact(workspace)
    assert artifact["competition"] is None
    assert artifact["submitted"] is False
    assert "competition_name" in artifact["reason"]
    assert api.calls == []


def test_get_score_type_error_from_a_null_date_records_a_contextual_reason(
    workspace: WorkspaceManager,
) -> None:
    """Two submissions, not one: `max` only invokes the key comparison when it
    has something to compare against, so a single-element list never raises."""
    api = _FakeApi(
        submissions=[_FakeSubmission(None, 0.8), _FakeSubmission(None, 0.9)],
    )
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["submitted"] is True
    assert artifact["lb_score"] is None
    assert "titanic" in artifact["reason"]
    assert "date" in artifact["reason"]


def test_get_score_type_error_from_an_unscored_submission_does_not_blame_date_ordering(
    workspace: WorkspaceManager,
) -> None:
    """One submission, `public_score` unset — the normal state in the window
    right after `competition_submit`, which is exactly when this node calls.
    `float(None)` raises `TypeError`, and with a single submission `max` never
    invokes the `date` key comparison at all, so the reason must not diagnose a
    date-ordering bug."""
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), None)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["submitted"] is True
    assert artifact["lb_score"] is None
    reason = artifact["reason"]
    assert "public_score" in reason
    assert "not yet a number" in reason
    assert "whose 'date' is None or otherwise unorderable" not in reason


def test_get_score_runtime_error_with_no_submissions_records_a_reason(
    workspace: WorkspaceManager,
) -> None:
    api = _FakeApi(submissions=[])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["submitted"] is True
    assert artifact["lb_score"] is None
    assert "No submissions found" in artifact["reason"]


def test_unexpected_api_exception_never_aborts_the_graph(workspace: WorkspaceManager) -> None:
    class _KaggleApiError(Exception):
        pass

    api = _FakeApi(submit_error=_KaggleApiError("503 Service Unavailable"))
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    delta = KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["submitted"] is False
    assert "503 Service Unavailable" in artifact["reason"]
    assert set(delta) == {"messages"}


def test_unwritable_workspace_still_returns_a_messages_delta(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 7 is terminal: an artifact write that fails must not abort the
    graph after `reviewer` and `report_writer` already produced the
    deliverables. The failure cannot be recorded in the file that failed to be
    written, so it rides the `messages` summary line instead."""
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)
    state = _state(workspace, current_iteration=1, best_score=0.9)

    def _refuse(self: WorkspaceManager, relative_path: str, data: dict) -> str:
        raise PermissionError(f"[Errno 13] Permission denied: '{self.workspace_path}'")

    monkeypatch.setattr(WorkspaceManager, "write_json", _refuse)

    delta = KaggleClientNode(api=api)(state)

    assert set(delta) == {"messages"}
    content = delta["messages"][0]["content"]
    assert ARTIFACT_PATH in content
    assert "could not be written" in content
    assert "PermissionError" in content
    assert str(workspace.workspace_path) not in content


def test_unopenable_workspace_still_returns_a_messages_delta(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`WorkspaceManager.__init__` creates the root directory, so an unwritable
    workspace raises before there is anywhere to record it — the other terminal
    path that must still return normally."""

    def _refuse(path: Any) -> WorkspaceManager:
        raise PermissionError(f"[Errno 13] Permission denied: '{path}'")

    monkeypatch.setattr(compute_base_module, "WorkspaceManager", _refuse)

    delta = KaggleClientNode(api=_FakeApi())(_state(workspace, current_iteration=1))

    assert set(delta) == {"messages"}
    content = delta["messages"][0]["content"]
    assert "PermissionError" in content
    assert str(workspace.workspace_path) not in content


def test_absolute_workspace_paths_never_reach_the_artifact_reason(
    workspace: WorkspaceManager,
) -> None:
    """The SDK is handed an absolute `file_name` and echoes it back in its error
    messages; `reports/kaggle_submission.json` ships inside the published
    deliverable repository, so the workspace root is scrubbed out of `reason`."""

    class _KaggleApiError(Exception):
        pass

    absolute = workspace.workspace_path / "experiments" / "exp_0" / "submission.csv"
    api = _FakeApi(submit_error=_KaggleApiError(f"400 Bad Request while reading {absolute}"))
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert str(workspace.workspace_path) not in artifact["reason"]
    assert "<workspace>/experiments/exp_0/submission.csv" in artifact["reason"]
    assert "400 Bad Request" in artifact["reason"]


def test_non_finite_lb_score_is_recorded_as_null(workspace: WorkspaceManager) -> None:
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), float("inf"))])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    artifact = _artifact(workspace)
    assert artifact["lb_score"] is None
    assert "not a finite number" in artifact["reason"]
    raw = (workspace.workspace_path / ARTIFACT_PATH).read_text(encoding="utf-8")
    assert "Infinity" not in raw
    assert json.loads(raw)["lb_score"] is None


@pytest.mark.parametrize("case", ["no_file", "bad_slug", "no_credentials", "api_exception"])
def test_artifact_is_written_on_every_failure_path(case: str, workspace: WorkspaceManager) -> None:
    _seed_score_evaluation(workspace, 0)
    state = _state(workspace, current_iteration=1, best_score=0.9)
    node: KaggleClientNode

    if case == "no_file":
        node = KaggleClientNode(api=_FakeApi())
    elif case == "bad_slug":
        _seed_submission(workspace, "exp_0")
        state["competition_name"] = "../evil"
        node = KaggleClientNode(api=_FakeApi())
    elif case == "no_credentials":
        _seed_submission(workspace, "exp_0")
        node = KaggleClientNode()
    else:
        _seed_submission(workspace, "exp_0")
        node = KaggleClientNode(api=_FakeApi(submit_error=Exception("boom")))

    node(state)

    assert (workspace.workspace_path / ARTIFACT_PATH).is_file()
    assert set(_artifact(workspace)) == _ARTIFACT_KEYS
    assert _artifact(workspace)["submitted"] is False


def test_module_path_is_not_absolute_in_the_artifact(workspace: WorkspaceManager) -> None:
    """The recorded `submission_file` must be workspace-relative so the artifact
    stays portable, even though the Kaggle API is handed the absolute path."""
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=0.9))

    assert not Path(_artifact(workspace)["submission_file"]).is_absolute()
    assert api.submitted_file == str(
        workspace.workspace_path / "experiments" / "exp_0" / "submission.csv"
    )


# -- defensive helpers ----------------------------------------------------


@pytest.mark.parametrize("value", [None, "3", 3.0, True, [], {}])
def test_coerce_iteration_defaults_to_zero_for_a_non_int(value: Any) -> None:
    assert kaggle_client_module._coerce_iteration(value) == 0


@pytest.mark.parametrize("value", [None, "0.5", True, float("nan"), float("-inf"), []])
def test_coerce_finite_float_rejects_everything_but_a_finite_number(value: Any) -> None:
    assert kaggle_client_module._coerce_finite_float(value) is None


@pytest.mark.parametrize("value", [None, "", "   ", 7, []])
def test_read_json_dict_degrades_for_a_non_string_path(
    value: Any, workspace: WorkspaceManager
) -> None:
    assert kaggle_client_module._read_json_dict(value, workspace) == {}


@pytest.mark.parametrize(
    "value", [None, "", "   ", 7, "..", "../evil", "/elsewhere/exp_1", "/", "."]
)
def test_experiment_basename_rejects_unusable_pointers(
    value: Any, workspace: WorkspaceManager
) -> None:
    assert kaggle_client_module._experiment_basename(value, workspace) is None


def test_best_experiment_path_equal_to_the_fallback_is_only_tried_once(
    workspace: WorkspaceManager,
) -> None:
    """Both candidates resolve to `experiments/exp_0/submission.csv`; the dedupe
    keeps the resolved path stable rather than producing a duplicate entry."""
    api = _FakeApi()
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0)

    KaggleClientNode(api=api)(
        _state(
            workspace,
            current_iteration=1,
            best_score=0.9,
            best_experiment_path="experiments/exp_0",
        )
    )

    assert _artifact(workspace)["submission_file"] == "experiments/exp_0/submission.csv"


def test_divergence_is_null_when_the_subtraction_overflows(workspace: WorkspaceManager) -> None:
    """Both operands are individually finite, but their difference is not —
    `score_evaluator`'s overflow precedent."""
    api = _FakeApi(submissions=[_FakeSubmission(datetime(2026, 8, 19), -1.7e308)])
    _seed_submission(workspace, "exp_0")
    _seed_score_evaluation(workspace, 0, "maximize")

    KaggleClientNode(api=api)(_state(workspace, current_iteration=1, best_score=1.7e308))

    artifact = _artifact(workspace)
    assert artifact["divergence"] is None
    assert artifact["divergence_flag"] is False
    assert "overflowed" in artifact["reason"]
