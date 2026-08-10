"""baseline_runner: executes the design `baseline_designer` wrote to
experiments/baseline/design.json against the frozen CV folds in
validation/fold_config.json, logs the run to MLflow, and writes
experiments/baseline/results.json.

Runs second in Pipeline Phase 3 (Baseline), which the supervisor guarantees only
executes at `current_iteration == 0` (CLAUDE.md invariant #4) — the resulting
`baseline_score`/`baseline_results_path` are the pipeline's single permanent
benchmark: set once here, never re-run, never overwritten (design.md § Phase 3).

`validation/fold_config.json` is read-only from this node's perspective — it is
frozen by `validation_strategist` in Pipeline Phase 1 (CLAUDE.md invariant #1) and
this node must never write to it, only read the fold indices out of it.

`mlflow` is imported only in this module (never in `src/nodes/compute/base.py`) —
`ComputeNode` itself stays free of any tracking/LLM dependency, per
`tests/unit/nodes/compute/test_base.py`'s "no LLM/langchain import" guard and the
base class's own module docstring ("no model, no AgentConfig... never imports from
src/llm or any langchain package").
"""

from __future__ import annotations

import json
from typing import Any

import mlflow

from src.config.settings import Settings
from src.nodes.compute.base import ComputeNode
from src.state import LabState
from src.tools.code_executor import execute

_DESIGN_PATH = "experiments/baseline/design.json"
_FOLDS_PATH = "validation/fold_config.json"
_RESULTS_PATH = "experiments/baseline/results.json"

# The generated training script is a fixed template, not built per-`design` via
# string interpolation: it reads `experiments/baseline/design.json` and
# `validation/fold_config.json` itself from the subprocess's cwd (the workspace
# root, per `execute(code, cwd=...)` below), exactly like the frozen-fold shape
# `validation_strategist`'s own LLM-authored scripts consume
# (`fold_indices: [{"train": [...], "val": [...]}, ...]`). Keeping it a constant
# avoids any string-escaping/injection concern from embedding LLM-authored design
# values (model name, hyperparameters) directly into source code.
_TRAINING_SCRIPT = """\
import glob
import json

import numpy as np
import pandas as pd

with open("experiments/baseline/design.json", encoding="utf-8") as f:
    design = json.load(f)
with open("validation/fold_config.json", encoding="utf-8") as f:
    folds = json.load(f)

model_name = design["model"]
hyperparameters = design["hyperparameters"]
features = design["features"]
target_column = design["target_column"]

csv_candidates = sorted(glob.glob("data/raw/**/*.csv", recursive=True))
train_candidates = [p for p in csv_candidates if "train" in p.lower()] or csv_candidates
if not train_candidates:
    raise FileNotFoundError("No CSV files found under data/raw/ to train the baseline on")
df = pd.read_csv(train_candidates[0])

feature_columns = [
    c for c in (df.columns if features == "all" else features) if c != target_column
]
X = pd.get_dummies(df[feature_columns])
y = df[target_column]

model_key = model_name.lower()
is_classification = y.dtype == object or str(y.dtype) in ("bool", "category") or y.nunique() <= 20


def resolve_model_class():
    # Unrecognized model names raise instead of silently falling back to a
    # different estimator: a silent substitution could either crash with a
    # confusing TypeError (hyperparameters meant for the real model don't match
    # the fallback's constructor) or, worse, train and log a "permanent
    # benchmark" score under the wrong model identity with no record anywhere
    # that a substitution happened.
    if "lightgbm" in model_key or "lgbm" in model_key:
        import lightgbm as lgb

        return lgb.LGBMClassifier if is_classification else lgb.LGBMRegressor
    if "xgboost" in model_key or "xgb" in model_key:
        import xgboost as xgb

        return xgb.XGBClassifier if is_classification else xgb.XGBRegressor
    if "random_forest" in model_key or "randomforest" in model_key:
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

        return RandomForestClassifier if is_classification else RandomForestRegressor
    if "logistic" in model_key:
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression
    if "linear" in model_key:
        from sklearn.linear_model import LinearRegression

        return LinearRegression
    raise ValueError(
        f"Unrecognized baseline model {model_name!r}; expected one of: "
        "lightgbm, xgboost, random_forest, logistic (classification), "
        "linear (regression)"
    )


model_cls = resolve_model_class()

fold_scores = []
for fold in folds["fold_indices"]:
    train_idx, val_idx = fold["train"], fold["val"]
    model = model_cls(**hyperparameters)
    model.fit(X.iloc[train_idx], y.iloc[train_idx])
    fold_scores.append(float(model.score(X.iloc[val_idx], y.iloc[val_idx])))

cv_score = float(np.mean(fold_scores))
print(
    json.dumps(
        {
            "cv_score": cv_score,
            "fold_scores": fold_scores,
            "model": model_name,
            "model_class": model_cls.__name__,
        }
    )
)
"""


def _last_nonblank_line(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("baseline_runner code produced no stdout output")
    return lines[-1]


class BaselineRunnerNode(ComputeNode):
    name = "baseline_runner"

    def run(self, state: LabState) -> dict[str, Any]:
        workspace = self.workspace(state)
        design = workspace.read_json(_DESIGN_PATH)
        # Read-only: proves the frozen folds exist and are readable, but this
        # node must never write validation/fold_config.json (CLAUDE.md invariant
        # #1). The generated script also reads it directly from disk for the
        # actual CV loop; this read is solely to fail fast/clearly if it's
        # missing, and to keep the "reads folds" contract testable independent
        # of the mocked `execute` call.
        workspace.read_json(_FOLDS_PATH)

        result = execute(_TRAINING_SCRIPT, cwd=str(workspace.workspace_path))
        if result.returncode != 0 or result.timed_out:
            raise ValueError(
                f"baseline_runner code execution failed "
                f"(returncode={result.returncode}, timed_out={result.timed_out}): {result.stderr}"
            )

        last_line = _last_nonblank_line(result.stdout)
        try:
            payload = json.loads(last_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"baseline_runner code did not print a JSON object as its last stdout "
                f"line: {exc}; line={last_line!r}"
            ) from exc

        if not isinstance(payload, dict) or "cv_score" not in payload:
            raise ValueError(
                f"baseline_runner stdout JSON must be an object with a 'cv_score' key, "
                f"got {payload!r}"
            )

        try:
            cv_score = float(payload["cv_score"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"baseline_runner stdout JSON 'cv_score' must be a number, "
                f"got {payload['cv_score']!r}"
            ) from exc

        fold_scores = payload.get("fold_scores", [])
        # Echoes back the class actually instantiated inside the subprocess, not
        # just `design["model"]`'s stated name — makes the permanent benchmark
        # self-verifying even when the requested model name did resolve.
        model_class = payload.get("model_class")

        settings = Settings.load()
        mlflow.set_tracking_uri(settings.workspace.mlflow_tracking_uri)
        with mlflow.start_run(run_name="baseline"):
            mlflow.log_params(design["hyperparameters"])
            if model_class:
                mlflow.log_param("model_class", model_class)
            mlflow.log_metric("cv_score", cv_score)

        results = {
            "cv_score": cv_score,
            "fold_scores": fold_scores,
            "model_class": model_class,
            "design": design,
        }
        results_path = workspace.write_json(_RESULTS_PATH, results)

        return {"baseline_score": cv_score, "baseline_results_path": results_path}
