"""code_critic: reviews the training script `coder` generated for the current
experiment against a reproducibility / leakage / faithfulness rubric, and
returns a `pass`/`iterate` verdict — re-invoking `coder` directly for up to
`max_retries` iterate cycles before forcing a pass (CLAUDE.md invariant #5).

Runs in exactly one phase: it is the **last** node of Pipeline Phase 5
(Implementation), per `config/phases/phase5_implementation.yaml`'s
`sequence: [specialist_selector, coder, code_critic]` and its
`critic: {node: code_critic, targets: [coder], max_retries: 3}` block.

Overrides `LLMNode.__call__` wholesale (rather than composing via the
`_build_messages`/`_write_output`/`_build_output_state` hooks) because it owns
its own retry control flow internally, re-invoking its target node directly
through `src.graph.node_resolver.resolve_node` — see context/decisions.md's
2026-08-05 T-009 entry ("a critic's own node function re-invokes its target
node(s) directly... entirely inside its own `__call__`"). `LLMNode.__init__`
is inherited unchanged (loads `AgentConfig`, prompt, LLM via `LLMFactory`,
`_max_messages_per_node`).

**Test patch points.** Because this custom `__call__` instantiates
`WorkspaceManager` directly in this module (not via the base class's own
`__call__`), unit tests must patch `src.nodes.llm.code_critic.WorkspaceManager`.
`resolve_node`, by contrast, must be patched at **`src.graph.node_resolver`**:
this module deliberately imports the `node_resolver` *module* and looks the
function up as an attribute at call time (the same binding style as
`src/graph/builder.py`), so `src.nodes.llm.code_critic.resolve_node` does not
exist. B-001 showed the import-time `from ... import resolve_node` form (still
used by `analysis_critic`) is import-order fragile and silently defeats a
`monkeypatch.setattr` on the resolver module. If a future task ever adds an
import-time binder here, `tests/unit/graph/test_checkpointer.py`'s
`_install_counting_resolver` must be updated to patch it too — this module
deliberately avoids creating that obligation.
`src.nodes.llm.base.LLMFactory`/`Settings` remain the correct patch points,
since `__init__` is inherited.

**No new `LabState` field.** `src/state.py` is a protected contract, and
`experiments` (the only field that could hold a generated-code pointer) has no
writer anywhere in `src/` yet. So this node locates the script the same way
the Phase 5 specialists locate their design: at a well-known workspace path,
`experiments/exp_{current_iteration}/train.py` (the `design.json` precedent —
see docs/pipeline.md § The `design.json` contract). When `coder` (T-029) does
start appending to `state["experiments"]`, the last entry's `path` is used in
preference, so the node works either way without a contract change.

**Retry budget** comes from `load_phase_config("phase5_implementation").critic
.max_retries`, *not* `Settings.execution.max_critic_retries`. Both are `3`
today, but the phase YAML is the contract that also names this node as the
phase's critic and its targets, and it allows a per-phase budget.

**Known `{iteration}` self-overwrite.** Nothing in `src/` increments
`state["current_iteration"]` yet (see the entry in context/discoveries.md), so
every cycle of a run writes `reports/code_critic_verdicts_iter0.json` and the
later write wins. Documented, not fixed here: a `current_iteration` writer is a
`src/graph/` change owned by the evaluation-phase tasks. Unlike
`analysis_critic` — which runs in two different phases at the same iteration
and therefore needs a `{phase}` placeholder — this node runs in exactly one
phase, so `{iteration}` alone is the right pattern and the base
`_resolve_output_path` suffices.

`extract_json_object`, `DEGRADE_ERRORS` and `read_fold_summary` are imported
from `_experiment_design.py` rather than re-copied: that module already owns
the fence-stripping/brace-salvage extractor and the degrade-error tuple, and
this node genuinely reads the same `design.json`/fold-config artifacts. Its
`specialist` parameter is an error-attribution label only, so passing
`"code_critic"` is correct usage. `_extract_verdict_data` wraps rather than
replaces it, adding a fence-anchored retry for the one response shape the shared
extractor cannot handle and this node provokes more than any other — see that
function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.config.loaders import load_phase_config
from src.graph import node_resolver
from src.nodes.llm._experiment_design import (
    DEGRADE_ERRORS,
    extract_json_object,
    read_fold_summary,
)
from src.nodes.llm.base import LLMNode, relative_to_workspace, trim_context
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

_NODE = "code_critic"
_PHASE_STEM = "phase5_implementation"
_EXPERIMENT_DIR_PATTERN = "experiments/exp_{iteration}"
_CODE_FILENAME = "train.py"
_DESIGN_FILENAME = "design.json"
_RESULTS_FILENAME = "results.json"
_MAX_CODE_CHARS = 20_000
_VALID_VERDICTS = ("pass", "iterate")


def _experiment_dir_from_state(state: dict[str, Any], workspace: WorkspaceManager) -> str | None:
    """Workspace-relative experiment *directory* derived from
    `state["experiments"][-1]["path"]`, or `None` when that pointer is absent
    or unusable.

    `coder` (T-029) has not landed, so whether it records the experiment
    directory or the `results.json` file inside it is not yet settled: a value
    carrying a suffix is treated as a file pointer and its parent is used, so
    both conventions work. Absolute values are re-relativized (the workspace
    writers return absolute paths), and anything that escapes the workspace or
    is otherwise unusable returns `None` so the caller falls back to the
    well-known iteration directory.
    """
    experiments = state.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        return None
    last = experiments[-1]
    if not isinstance(last, dict):
        return None
    raw_path = last.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    try:
        reference = relative_to_workspace(raw_path.strip(), workspace)
    except DEGRADE_ERRORS:
        # A path recorded against a different (e.g. moved) workspace root makes
        # `Path.relative_to` raise `ValueError`; degrade to the fallback rather
        # than aborting the review.
        return None

    candidate = Path(reference)
    # Only the traversal guard is needed: `relative_to_workspace` either returns
    # an already-relative path unchanged or `str(Path.relative_to(root))`, both
    # relative — an absolute input that does not sit under the root raises
    # `ValueError`, caught above. A `..` component, by contrast, really does
    # survive both branches, so this is the load-bearing escape check.
    if ".." in candidate.parts:
        return None
    if candidate.suffix:
        candidate = candidate.parent
    text = str(candidate)
    if not text or text == ".":
        return None
    return text


def _candidate_experiment_dirs(state: dict[str, Any], workspace: WorkspaceManager) -> list[str]:
    """Experiment directories to try, best guess first: the state-recorded
    pointer (when usable), then the well-known iteration directory."""
    iteration = state.get("current_iteration", 0)
    candidates = [
        _experiment_dir_from_state(state, workspace),
        _EXPERIMENT_DIR_PATTERN.format(iteration=iteration),
    ]
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _read_artifact(workspace: WorkspaceManager, directory: str, filename: str) -> str | None:
    """Read one artifact as raw text, or `None` if it cannot be read.

    Catches `DEGRADE_ERRORS`, not bare `OSError`: a non-UTF-8 artifact raises
    `UnicodeDecodeError` (a `ValueError`), and `WorkspaceManager._resolve`
    raises `ValueError` for a path it considers absolute or traversing.
    """
    try:
        return workspace.read_text(f"{directory}/{filename}")
    except DEGRADE_ERRORS:
        return None


def _truncate(text: str, label: str = "generated code") -> str:
    """Bound one artifact injected into the prompt.

    Applied to **all three** file artifacts, not just the code. `train.py` needs
    it because the prompt window has to stay bounded (same spirit as
    `read_fold_summary` refusing to inline `fold_indices`); `results.json` needs
    it more, because it is written by the generated script — the least-trusted
    component here — and normally carries the OOF predictions, so a multi-megabyte
    one would otherwise become a multi-megabyte single `invoke`. `design.json` is
    genuinely small and structured, and is capped only for uniformity.

    Trade-off, recorded deliberately: a violation sitting past the cap can be
    missed. That is acceptable because 20 000 characters is far past any plausible
    single training script, and the in-band marker tells the critic its view is
    partial so it says so in the feedback rather than passing silently.
    """
    if len(text) <= _MAX_CODE_CHARS:
        return text
    marker = f"\n\n... (truncated at {_MAX_CODE_CHARS} characters of {label})"
    return text[:_MAX_CODE_CHARS] + marker


def _read_code(dirs: list[str], workspace: WorkspaceManager) -> tuple[str, bool, str]:
    """First readable `train.py` across the candidate directories.

    Returns `(text, available, directory)`. `directory` is the one that actually
    yielded the script — the caller reads `design.json`/`results.json` from *that
    same* directory, so the critic can never review one experiment's code against
    another experiment's design (a review of exp_7's LightGBM script against
    exp_0's distilbert design is not hypothetical: nothing in `src/` increments
    `current_iteration` yet, so the well-known fallback is permanently `exp_0`
    while `coder` will name later directories `exp_1`, `exp_2`, …). When no
    directory yields the script, the first candidate is returned so the context
    reads still come from a single, named place.

    `available` is recorded on each attempt as `code_available`, which makes the
    degrade path assertable without branching the control flow — the *verdict*
    consequence stays prompt-driven (the prompt treats an unreadable code
    section as a hard iterate), exactly as `analysis_critic` handles its own
    `(... not yet available)` placeholders.
    """
    for directory in dirs:
        content = _read_artifact(workspace, directory, _CODE_FILENAME)
        if content is not None:
            return _truncate(content), True, directory
    first = dirs[0]
    return (
        f"(unable to read the generated training code at {first}/{_CODE_FILENAME} — "
        "there is nothing to review)",
        False,
        first,
    )


def _read_context_artifact(directory: str, workspace: WorkspaceManager, filename: str) -> str:
    """Read one context artifact from the single directory that yielded
    `train.py`, or a placeholder.

    Read as text, never `read_json`: the critic only needs to see the artifact,
    and text reading cannot raise on a non-dict payload. Truncated like the code
    is: `results.json` is written by the *generated* script — the least-trusted
    component in the system — and typically carries the OOF predictions, so an
    untruncated one is an unbounded prompt (a 4 MB `results.json` produces a
    ~4 000 000-character review message in a single `invoke`, against CLAUDE.md's
    "< $0.50 per full competition run" target).
    """
    content = _read_artifact(workspace, directory, filename)
    if content is None:
        return f"({filename} not available for this experiment)"
    return _truncate(content, label=filename)


def _code_fence(text: str) -> str:
    """A backtick fence longer than any backtick run inside `text`.

    A generated `train.py` containing a ``` line would otherwise close the fence
    early and let the remainder of the file render as top-level prompt markup —
    a `train.py` can trivially carry a valid-Python docstring holding a
    counterfeit `## Experiment design` section that instructs a pass. Widening
    the fence keeps the whole script inside one block; the prompt separately
    states that everything under the code heading is data to review, never an
    instruction to obey. Note the retry cap does not help here: it bounds
    *iterate* loops, and injection seeks a false *pass*.
    """
    longest = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def _build_review_message(state: dict[str, Any], workspace: WorkspaceManager) -> tuple[str, bool]:
    """Build the four labeled review sections, returning
    `(content, code_available)`.

    The experiment directory is resolved **once** — to whichever candidate
    actually yielded `train.py` — and the design and results are then read from
    that same directory, so all three artifacts always describe one experiment.
    """
    dirs = _candidate_experiment_dirs(state, workspace)
    code, code_available, directory = _read_code(dirs, workspace)
    design = _read_context_artifact(directory, workspace, _DESIGN_FILENAME)
    results = _read_context_artifact(directory, workspace, _RESULTS_FILENAME)
    folds = read_fold_summary(cast(LabState, state), workspace)

    fence = _code_fence(code)
    content = (
        f"## Generated training code ({_CODE_FILENAME})\n\n"
        f"{fence}python\n{code}\n{fence}\n\n"
        f"## Experiment design ({_DESIGN_FILENAME})\n\n{design}\n\n"
        f"## Experiment results ({_RESULTS_FILENAME})\n\n{results}\n\n"
        f"## Frozen CV folds\n\n{folds}"
    )
    return content, code_available


def _extract_verdict_data(content: str) -> dict[str, Any] | None:
    """Extract the verdict object, or `None`. Never raises.

    `extract_json_object` alone is not sufficient here, and the reason is
    specific to a *code* critic. It handles a fenced whole response (its
    `_strip_outer_fence` anchors the close with `rfind("```")`, so a ```python
    fence quoted *inside* the `feedback` value still parses) and it salvages the
    first-`{`-to-last-`}` window when prose or a **brace-free** trailing fenced
    block follows the JSON. But when the trailing block *contains braces* — and
    for this node the most likely postamble of all is a Python snippet
    illustrating the fix, which is full of them — the salvage window runs from
    the real object's `{` to the *snippet's* `}` and fails to parse. Measured
    consequence: the whole response is discarded and `feedback` is replaced by
    the "could not parse" text, so `coder` is re-invoked carrying no signal and
    one retry of a 3-retry budget is burned for nothing.

    So on failure this retries on the prefix before each fence marker, which
    recovers exactly that shape. (For the record, `analysis_critic`'s
    `_fence_candidates` does not handle these shapes either — it returns `None`
    for both the brace-free and the brace-bearing trailing-fence response,
    because it only builds extra candidates when the response *starts* with a
    fence. The two nodes fail differently; neither strictly dominates, which is
    another argument for the shared-helper hoist proposed in
    `context/discoveries.md`.)
    """
    try:
        return extract_json_object(content, _NODE)
    except DEGRADE_ERRORS:
        pass

    # Fence-anchored fallback: try the prefix before each fence marker, nearest
    # first. `extract_json_object` is reused (not `json.loads`) so each prefix
    # still gets fence-stripping and brace salvage.
    search_from = 0
    while True:
        marker = content.find("```", search_from)
        if marker == -1:
            return None
        prefix = content[:marker].strip()
        search_from = marker + 3
        if not prefix:
            continue
        try:
            return extract_json_object(prefix, _NODE)
        except DEGRADE_ERRORS:
            continue


def _parse_verdict(content: str, allowed_targets: tuple[str, ...]) -> tuple[str, str, str]:
    """Parse the LLM's verdict JSON. Never raises `ValueError`, `OSError` or
    `RecursionError` (`_experiment_design.DEGRADE_ERRORS`) — always returns a
    normalized `(verdict, feedback, target_node)` triple:

    - `verdict` is always exactly `"pass"` or `"iterate"` (malformed JSON, a
      non-object payload, or any other value normalizes to `"iterate"`).
    - `target_node` is always exactly one of `allowed_targets` (falling back to
      `allowed_targets[0]`). The prompt asks for `verdict`+`feedback` only —
      honoring an optional `target_node` costs one line and keeps the node
      correct if a future phase YAML gives this critic more than one target.
    - `feedback` is never empty (a default is synthesized, worded per verdict).

    The caught set is `DEGRADE_ERRORS`, not a bare `ValueError`: `json.loads`
    raises **`RecursionError`** (not a `ValueError` subclass) on a deeply nested
    payload, reproducible at ~2 400 characters and therefore reachable inside
    this agent's `max_tokens`. Letting it escape would abort the entire graph run
    from the one node whose whole contract is to degrade, and it would abort
    *before* any verdict record is written.
    """
    fallback_target = allowed_targets[0] if allowed_targets else ""

    data = _extract_verdict_data(content)

    if data is None:
        return (
            "iterate",
            f"{_NODE}: could not parse a JSON verdict object from the LLM response "
            "(parse failure); treating this as a hard iterate signal.",
            fallback_target,
        )

    raw_verdict = data.get("verdict")
    verdict = raw_verdict if raw_verdict in _VALID_VERDICTS else "iterate"

    raw_target = data.get("target_node")
    target_node = raw_target if raw_target in allowed_targets else fallback_target

    feedback = data.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip():
        feedback = (
            f"{_NODE}: pass verdict with no specific feedback provided."
            if verdict == "pass"
            else f"{_NODE}: iterate verdict with no specific feedback provided."
        )

    return verdict, feedback, target_node


class CodeCriticNode(LLMNode):
    name = "code_critic"

    def __call__(self, state: LabState) -> dict[str, Any]:
        workspace = WorkspaceManager(state["workspace_path"])

        critic_config = load_phase_config(_PHASE_STEM).critic
        if critic_config is None:
            raise ValueError(f"{_NODE} invoked but {_PHASE_STEM}.yaml has no 'critic' block")

        max_retries = critic_config.max_retries
        allowed_targets = critic_config.targets
        fallback_target = allowed_targets[0] if allowed_targets else ""

        # Global safety cap: bounds the loop even under a pathological LLM
        # response pattern, guarding CLAUDE.md invariant #5 ("no infinite
        # loops"). Reachability note (scoped as `analysis_critic` scopes its
        # own): for **any input the LLM can currently produce**, the `for...else`
        # branch below is unreachable — because `target_node` is always
        # normalized to one of the finite `allowed_targets`, a pigeonhole
        # argument shows the per-target `count >= max_retries` guard alone
        # bounds any target pattern within this many cycles (with N targets,
        # N * (max_retries + 1) cycles cannot be filled without some target
        # reaching `max_retries + 1` visits first), and here N == 1.
        #
        # It is *not* unreachable in general: `load_phase_config` does not
        # validate `max_retries`, so a phase YAML carrying a negative value
        # makes `max_total_cycles <= 0`, the loop body never runs, and this node
        # emits a forced pass having made zero LLM calls and reviewed nothing.
        # That is a misconfiguration rather than an LLM behavior — flagged to the
        # `src/config/` owner in `context/discoveries.md` — but the branch is
        # live, so it must not reference any loop-local name.
        max_total_cycles = (max_retries + 1) * max(len(allowed_targets), 1)

        retry_counts: dict[str, int] = {}
        working_state: dict[str, Any] = dict(state)
        accumulated_messages: list[BaseMessage] = []
        attempts: list[dict[str, Any]] = []

        for _cycle in range(max_total_cycles):
            trimmed = trim_context(working_state.get("messages", []), self._max_messages_per_node)
            review, code_available = _build_review_message(working_state, workspace)
            messages: list[BaseMessage] = [
                SystemMessage(content=self.system_prompt),
                *trimmed,
                HumanMessage(content=review),
            ]

            response = self.llm.invoke(messages)
            accumulated_messages.append(response)
            working_state["messages"] = [*working_state.get("messages", []), response]

            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            verdict, feedback, target_node = _parse_verdict(content, allowed_targets)

            if verdict == "pass":
                attempts.append(
                    {
                        "verdict": "pass",
                        "feedback": feedback,
                        "target_node": target_node,
                        "code_available": code_available,
                    }
                )
                break

            # verdict == "iterate"
            count = retry_counts.get(target_node, 0)
            if count >= max_retries:
                attempts.append(
                    {
                        "verdict": "pass",
                        "feedback": (
                            f"{_NODE}: retry budget exhausted for '{target_node}' after "
                            f"{max_retries} iterate cycle(s); forcing pass. "
                            f"Last feedback: {feedback}"
                        ),
                        "target_node": target_node,
                        "forced_pass": True,
                        "code_available": code_available,
                    }
                )
                break

            retry_counts[target_node] = count + 1
            # No `try/except` around the target invocation: `coder` has no
            # documented write-once exception (`FoldsAlreadyFrozenError` is
            # `validation_strategist`-specific and deliberately not mirrored
            # here), so a real crash in the target must surface rather than be
            # laundered into a forced pass. Resolved through the module
            # attribute per this module's docstring (B-001).
            delta = node_resolver.resolve_node(target_node)(cast(LabState, working_state))

            attempts.append(
                {
                    "verdict": "iterate",
                    "feedback": feedback,
                    "target_node": target_node,
                    "code_available": code_available,
                }
            )

            target_delta = dict(delta) if isinstance(delta, dict) else {}
            target_messages = target_delta.pop("messages", None)
            # Unlike `analysis_critic`, this node genuinely *relies* on the
            # retried target's non-"messages" delta locally: `coder` appends to
            # `experiments`, and picking that up is what lets the next review
            # cycle re-read the regenerated `train.py` when its recorded path
            # moves. Only `working_state` ever sees those keys — they are never
            # part of this node's own returned delta (messages-only contract at
            # the bottom of `__call__`).
            working_state.update(target_delta)
            if target_messages:
                accumulated_messages.extend(target_messages)
                working_state["messages"] = [*working_state.get("messages", []), *target_messages]
        else:
            # `code_available` is `None`, not a loop variable: when
            # `max_total_cycles <= 0` the loop body never ran, so every
            # loop-local name is unbound here. `None` reads as "no cycle
            # completed, so availability was never established".
            attempts.append(
                {
                    "verdict": "pass",
                    "feedback": (
                        f"{_NODE}: global safety cap of {max_total_cycles} review cycle(s) "
                        "reached without a pass verdict; forcing pass."
                    ),
                    "target_node": fallback_target,
                    "forced_pass": True,
                    "code_available": None,
                }
            )

        # `state`, not `working_state`: a target's delta must not be able to
        # move the record path mid-loop (matching `analysis_critic`).
        relative_path = self._resolve_output_path(state)
        record = {
            "phase": _PHASE_STEM,
            "targets": list(allowed_targets),
            "attempts": attempts,
            "final_verdict": attempts[-1],
        }
        workspace.write_json(relative_path, record)

        return {"messages": accumulated_messages}
