"""feature_importance_extractor: the second node of Pipeline Phase 6
(Evaluation), immediately after `score_evaluator`. Extracts a pre-computed
`{feature: importance}` payload from the current experiment's `results.json`
and writes a ranked report — it never fits a model or computes SHAP itself.

Runs second in `config/phases/phase6_evaluation.yaml`'s sequence, pure Python,
no LLM. **Always returns `{}`**: no `LabState` field exists for this
artifact (there is nothing for a downstream node to read off the state
object), so writing the report to the workspace is this node's only effect.

## Extraction, not computation

`design.md`'s "Python + SHAP" phrasing for this node is stale for this
implementation: **no `shap` import anywhere** in this module. `shap` is
declared in `pyproject.toml` but was not guaranteed to be installed in every
environment this graph builds in, and `src/graph/node_resolver.py:75-82`
turns a missing transitive import in a landed node module into a
`GraphBuilderError` that fails the *whole graph build*, not just this node.
Since `results.json`'s `feature_importance` field is expected to already
carry per-feature importances (from whichever estimator the generated
`train.py` trained — `coder`'s contract, T-029, not yet landed), there is no
lazy-import call site to guard here: this node only reads and ranks what is
already on disk. `docs/pipeline.md` carries this clarification since
`design.md` is not in pipeline-agent's owned folders (CLAUDE.md's module
ownership table).

## Model-family gate is an explicit allow-list

Only tree-ensemble model families produce a meaningful feature-importance
ranking; a linear model's coefficients or a neural net's weights are not
comparable "importances" in the same sense. `_TREE_MODEL_FAMILIES` is a
closed vocabulary sourced from `classical_ml_specialist.py:42-47` (the
tabular families: `xgboost`, `lightgbm`, `catboost`, `extra_trees`) plus
`timeseries_specialist.py:148-172`'s `gradient_boosting_lags` (a gradient
boosting model over lag features — a tree ensemble despite the timeseries
framing). `linear_lags` is deliberately excluded: it is a linear model over
lag features, not a tree.

This is an **explicit allow-list, not a neural/linear deny-list** on purpose:
an unrecognized or future model family (e.g. a new specialist's model type)
skips safely with a clear reason rather than attempting an extraction that
would silently produce a meaningless ranking.

Experiment-directory resolution and degrade-safe JSON reading are delegated to
`src.nodes.compute._evaluation_common` — see that module's docstring.
"""

from __future__ import annotations

import math
from typing import Any

from src.nodes.compute import _evaluation_common
from src.nodes.compute.base import ComputeNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

# Closed vocabulary of tree-ensemble model families that produce a meaningful
# feature-importance ranking — see the module docstring's "Model-family gate"
# section for the sourcing of each entry.
_TREE_MODEL_FAMILIES = frozenset(
    {
        "xgboost",
        "lightgbm",
        "catboost",
        "extra_trees",
        "gradient_boosting_lags",
    }
)

_OUTPUT_PATTERN = "reports/feature_importance_{iteration}.json"


def _normalize_model_family(value: Any) -> str | None:
    """`value.strip().lower()` for a non-empty `str`, else `None`.

    Raw `design.json` `model_family` values are already canonical tokens
    (produced by `_experiment_design.normalize_model_family` at write time),
    so no alias matching is needed here — only case/whitespace tolerance."""
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def _is_feature_name_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _extract_importances(payload: Any, feature_names: Any) -> dict[str, float] | None:
    """Validate and coerce a `results.json` `feature_importance` payload into
    `{feature: abs(importance)}`, or `None` if nothing usable survives.

    `payload` must be a non-empty `dict`. Per `(key, value)` pair: a
    non-`str`/empty key is skipped; if `feature_names` is a valid `list[str]`,
    a key absent from it is skipped (an unfiltered payload is used as-is when
    `feature_names` is absent or malformed); a non-numeric, `bool`, or
    non-finite value is skipped. Surviving values are kept as
    `abs(float(value))` — ranking is by magnitude, not by sign (a feature
    that pushes the prediction down is exactly as "important" as one that
    pushes it up).
    """
    if not isinstance(payload, dict) or not payload:
        return None

    allowed_features = feature_names if _is_feature_name_list(feature_names) else None

    extracted: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        if allowed_features is not None and key not in allowed_features:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            continue
        extracted[key] = abs(numeric)

    return extracted or None


def _rank_importances(extracted: dict[str, float]) -> list[dict[str, Any]]:
    """Sort descending by importance and attach a 1-indexed `rank` plus a
    `normalized_importance` (share of the total; `0.0` for every entry when
    the total is `0.0`, avoiding a division by zero when every surviving
    importance happens to be zero)."""
    total = sum(extracted.values())
    ranked_items = sorted(extracted.items(), key=lambda item: item[1], reverse=True)
    return [
        {
            "feature": feature,
            "importance": importance,
            "normalized_importance": (importance / total) if total > 0 else 0.0,
            "rank": rank,
        }
        for rank, (feature, importance) in enumerate(ranked_items, start=1)
    ]


class FeatureImportanceExtractorNode(ComputeNode):
    name = "feature_importance_extractor"

    def run(self, state: LabState) -> dict[str, Any]:
        workspace = self.workspace(state)
        experiment_dir, results = _evaluation_common.read_experiment_results(dict(state), workspace)
        design = _evaluation_common.read_json_dict(
            f"{experiment_dir}/{_evaluation_common.DESIGN_FILENAME}", workspace
        )
        model_family = _normalize_model_family(design.get("model_family"))
        iteration = _evaluation_common.resolve_iteration(dict(state))

        if model_family not in _TREE_MODEL_FAMILIES:
            reason = (
                "design.json missing/unreadable 'model_family'"
                if model_family is None
                else f"model_family {model_family!r} is not in the tree allow-list"
            )
            self._write_skip(workspace, iteration, experiment_dir, model_family, reason)
            return {}

        ranked_source = _extract_importances(
            results.get("feature_importance"), results.get("feature_names")
        )
        if ranked_source is None:
            self._write_skip(
                workspace,
                iteration,
                experiment_dir,
                model_family,
                "'feature_importance' payload in results.json is absent or malformed",
            )
            return {}

        artifact = {
            "skipped": False,
            "reason": None,
            "iteration": iteration,
            "experiment_dir": experiment_dir,
            "model_family": model_family,
            "features": _rank_importances(ranked_source),
        }
        workspace.write_json(_OUTPUT_PATTERN.format(iteration=iteration), artifact)
        return {}

    @staticmethod
    def _write_skip(
        workspace: WorkspaceManager,
        iteration: int,
        experiment_dir: str,
        model_family: str | None,
        reason: str,
    ) -> None:
        artifact = {
            "skipped": True,
            "reason": reason,
            "iteration": iteration,
            "experiment_dir": experiment_dir,
            "model_family": model_family,
            "features": [],
        }
        workspace.write_json(_OUTPUT_PATTERN.format(iteration=iteration), artifact)
