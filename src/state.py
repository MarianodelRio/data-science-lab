"""Shared LangGraph state contract for the pipeline.

`LabState` is the single coordinator state threaded through every node in the
graph. It is a **type-only** module: no I/O, no LLM calls, no side effects.
Every node reads/writes this contract, so it is protected — see
`design.md` § Shared contracts for the source of truth this module mirrors,
and CLAUDE.md's "Protected contracts" list.

`LabState` is intentionally lightweight: it holds file paths, scalar scores,
and control fields only. Large data (EDA reports, experiment results, code)
lives on disk in the workspace; the state only holds pointers to it.
"""

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class LabState(TypedDict):
    # Input
    competition_name: str
    workspace_path: str

    # File pointers (content lives in workspace, state holds the path)
    eda_report_path: str
    problem_definition_path: str
    validation_config_path: str  # immutable after Phase 1
    baseline_results_path: str  # permanent benchmark, set once
    solution_plan_path: str
    feature_spec_path: str

    # Control
    phase: str
    current_iteration: int
    max_iterations: int
    iterations_without_improvement: int

    # Scores (floats only)
    baseline_score: float  # single permanent benchmark
    best_score: float
    last_score: float
    score_delta: float

    # Experiment index (metadata only, full results in workspace files)
    experiments: list[dict]  # [{id, path, cv_score, iteration, model}]
    best_experiment_path: str  # only updated when score > best_score

    # Human checkpoint
    checkpoint_summary: str  # markdown rendered in UI
    human_feedback: str

    # LLM context (trimmed per node: last N messages + node-specific input)
    messages: Annotated[list, add_messages]


def new_state(competition_name: str, workspace_path: str, *, max_iterations: int = 10) -> LabState:
    """Build a fresh `LabState` for a new competition run.

    All scores/paths start at their zero values; `best_score` starts at
    negative infinity so the first real experiment always counts as an
    improvement. `max_iterations` defaults to 10, matching
    `config/settings.yaml`'s `execution.max_iterations`.
    """
    return LabState(
        competition_name=competition_name,
        workspace_path=workspace_path,
        eda_report_path="",
        problem_definition_path="",
        validation_config_path="",
        baseline_results_path="",
        solution_plan_path="",
        feature_spec_path="",
        phase="",
        current_iteration=0,
        max_iterations=max_iterations,
        iterations_without_improvement=0,
        baseline_score=0.0,
        best_score=float("-inf"),
        last_score=0.0,
        score_delta=0.0,
        experiments=[],
        best_experiment_path="",
        checkpoint_summary="",
        human_feedback="",
        messages=[],
    )
