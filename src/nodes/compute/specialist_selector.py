"""specialist_selector: reads the problem definition + solution plan and picks
exactly one specialist to activate this iteration of Pipeline Phase 5
(Implementation), then dispatches to it directly.

Deliberately reads TWO upstream artifacts, not just `solution_plan.json` (the
task file's original wording): `solution_plan.json` (written by
`solution_architect`, T-021) has no `problem_type` field, only
`model_families`/`order`/`ensembling_strategy`/`rationale` — the problem type
itself lives in `problem_definition.json` (written by `problem_framer`, Phase
1), pointed at by `state["problem_definition_path"]`. Both reads independently
degrade to `{}` on an unset path, a missing/unreadable file, or non-dict JSON
content -- this node never raises for a partially-completed upstream phase,
mirroring `feature_engineer._read_solution_plan`/`_read_eda_report`'s
degrade-on-missing convention (T-022), though this module writes its own
local reader (returning a parsed `dict`, not formatted text) rather than
importing anything from `src.nodes.llm` -- doing so would transitively pull in
`langchain_core`/`src.llm`, violating this node's "no LLM import" contract.

Specialist choice is a deterministic 4-branch keyword precedence over a single
normalized text blob (`problem_type` + `model_families` + `order` +
`rationale`, lowercased, separators collapsed to spaces), matched with
word-boundary regexes -- same curated-keyword-list convention as
`feature_engineer._TARGET_ENCODING_KEYWORDS`/`_is_target_encoding_method`
(T-022), applied here to specialist routing instead of target-encoding
detection:

1. timeseries keywords ("time series forecasting", "forecast", "arima",
   "prophet") -> `timeseries_specialist`
2. NLP keywords ("nlp", "text", "bert", "transformer", "tfidf", "tf idf",
   "embedding") -> `nlp_specialist`
3. deep-learning keywords ("neural", "cnn", "rnn", "deep learning",
   "pytorch", "lstm") -> `deep_learning_specialist`
4. no match -> `classical_ml_specialist` (default)

Timeseries is checked before deep learning, and NLP before deep learning, on
purpose: an LSTM/transformer plan for a forecasting or text problem should
route to `timeseries_specialist`/`nlp_specialist` (the problem-type-driven
specialist), not `deep_learning_specialist` (an architecture-driven label that
can co-occur with either) -- routing by problem type is the more actionable
specialist boundary. See context/decisions.md's 2026-08-11 T-023 entry.

Independent of 1-4: `ensemble_specialist` is selected instead, but only when
BOTH (a) `state["experiments"]` already has >= 2 entries, AND (b)
`solution_plan.json`'s `ensembling_strategy` field is a non-empty string that
does not itself say "no ensembling" (checked via the same normalized-blob
word-boundary approach against `"no ensembl"`, a common prefix of both "no
ensembling" and "no ensemble"). Condition (a) is checked first and
short-circuits immediately when fewer than 2 experiments exist --
`ensemble_specialist` must never be selected before then, regardless of what
`ensembling_strategy` says (the task's central, non-negotiable done-when
criterion).

Once a specialist name is chosen, `run` dispatches to it directly via
`resolve_node` (src/graph/node_resolver.py) exactly once -- no loop, no retry
-- and returns that specialist's own delta, defensively guarded to always be
a `dict` (mirrors the single-call/merge shape at `analysis_critic.__call__`'s
`resolve_node(target_node)(...)` call site, T-016/T-009, minus its
retry-loop/max_retries control flow, which this node has no need for: it
dispatches once per invocation, not up to N times). Every one of the 5
specialist names resolves to `NoOpNode` today (T-024-T-028 haven't landed
yet) -- this is `resolve_node`'s documented "not implemented yet" fallback,
not a bug in this module.

`config/phases/phase5_implementation.yaml`'s `nodes`/`sequence` deliberately
no longer lists the 5 specialist names (see the matching 2026-08-11 T-023
decisions.md entry): this node owns dispatch entirely internally, so listing
a specialist there too would make `src/graph/phases/generic.py` invoke it a
*second* time as a real graph edge once a future task lands it for real,
contradicting design.md's "one specialist at a time."
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.graph.node_resolver import resolve_node
from src.nodes.compute.base import ComputeNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

# -- keyword precedence tables (see module docstring for the full rationale) --

_TIMESERIES_KEYWORDS = (
    "time series forecasting",
    "forecast",
    "arima",
    "prophet",
)

_NLP_KEYWORDS = (
    "nlp",
    "text",
    "bert",
    "transformer",
    "tfidf",
    "tf idf",
    "embedding",
)

_DEEP_LEARNING_KEYWORDS = (
    "neural",
    "cnn",
    "rnn",
    "deep learning",
    "pytorch",
    "lstm",
)

# Deliberately only a left word-boundary: "ensembl" is a common prefix of both
# "ensembling" and "ensemble" (the two forms an LLM-authored `ensembling_strategy`
# string is expected to use), and there is no word-character transition between
# "ensembl" and either suffix for a trailing `\b` to match against.
_NO_ENSEMBLE_PATTERN = re.compile(r"\bno ensembl")


def _normalize(text: str) -> str:
    """Lowercase, collapse `-`/`_` separators to spaces, collapse whitespace.

    Same normalization convention as
    `feature_engineer._is_target_encoding_method` (T-022): makes
    "time-series", "time_series", and "time series" all match identically
    against the keyword tables above.
    """
    normalized = re.sub(r"[-_]+", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_any(blob: str, keywords: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(keyword)}\b", blob) for keyword in keywords)


def _to_relative(path: str, workspace: WorkspaceManager) -> str:
    """Re-relativize an absolute path (as written by `WorkspaceManager.write_json`'s
    return value, stored verbatim into `LabState` path fields) against the
    current workspace root; an already-relative path passes through unchanged.
    Own local copy -- see module docstring on why this doesn't import
    `src.nodes.llm.base.relative_to_workspace` instead."""
    p = Path(path)
    if not p.is_absolute():
        return path
    try:
        return str(p.relative_to(workspace.workspace_path))
    except ValueError:
        return path


def _read_json_dict(path: str, workspace: WorkspaceManager) -> dict[str, Any]:
    """Read `path` (a `LabState` path-field value) as a JSON object, degrading
    to `{}` -- never raising -- on an unset/empty path, a missing or
    unreadable file (`OSError`), malformed JSON (`json.JSONDecodeError`), an
    invalid relative path (`ValueError`, from `WorkspaceManager._resolve`), or
    JSON content that parses but isn't an object (e.g. a bare list/string)."""
    if not path:
        return {}
    try:
        relative_path = _to_relative(path, workspace)
        data = workspace.read_json(relative_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _build_signal_blob(problem_definition: dict[str, Any], solution_plan: dict[str, Any]) -> str:
    """One normalized text blob combining `problem_type` (problem_definition.json)
    with `model_families`/`order`/`rationale` (solution_plan.json) -- the input
    the 4-branch keyword precedence matches against."""
    parts = [
        _coerce_str(problem_definition.get("problem_type")),
        *_coerce_str_list(solution_plan.get("model_families")),
        *_coerce_str_list(solution_plan.get("order")),
        _coerce_str(solution_plan.get("rationale")),
    ]
    return _normalize(" ".join(parts))


def _select_by_signal(blob: str) -> str:
    """The 4-branch precedence (see module docstring): first match wins."""
    if _contains_any(blob, _TIMESERIES_KEYWORDS):
        return "timeseries_specialist"
    if _contains_any(blob, _NLP_KEYWORDS):
        return "nlp_specialist"
    if _contains_any(blob, _DEEP_LEARNING_KEYWORDS):
        return "deep_learning_specialist"
    return "classical_ml_specialist"


def _should_ensemble(state: LabState, solution_plan: dict[str, Any]) -> bool:
    """`ensemble_specialist` eligibility: >= 2 experiments (checked first,
    short-circuits immediately when false -- this must never be bypassed by
    anything `ensembling_strategy` says) AND a non-empty `ensembling_strategy`
    that doesn't itself signal "no ensembling"."""
    experiments = state.get("experiments")
    if not isinstance(experiments, list) or len(experiments) < 2:
        return False

    strategy = solution_plan.get("ensembling_strategy")
    if not isinstance(strategy, str) or not strategy.strip():
        return False

    normalized_strategy = _normalize(strategy)
    return not _NO_ENSEMBLE_PATTERN.search(normalized_strategy)


class SpecialistSelectorNode(ComputeNode):
    name = "specialist_selector"

    def run(self, state: LabState) -> dict[str, Any]:
        workspace = self.workspace(state)
        problem_definition = _read_json_dict(
            str(state.get("problem_definition_path") or ""), workspace
        )
        solution_plan = _read_json_dict(str(state.get("solution_plan_path") or ""), workspace)

        if _should_ensemble(state, solution_plan):
            chosen = "ensemble_specialist"
        else:
            blob = _build_signal_blob(problem_definition, solution_plan)
            chosen = _select_by_signal(blob)

        delta = resolve_node(chosen)(state)
        return dict(delta) if isinstance(delta, dict) else {}
