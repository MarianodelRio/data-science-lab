# Data Science Lab — IDEA.md

## What problem does this solve?

Doing data science well requires coordinating many parallel workstreams: exploring data, researching prior art, engineering features, training models, diagnosing failures, and iterating. A single person or small team bottlenecks on each step sequentially. This system implements a full data science team as a network of specialized AI agents built on LangGraph — each agent owns a specific role, they communicate through shared state, and the whole system can take a problem + dataset from raw input to a submission-ready solution, iterating autonomously until the user decides to stop.

The primary learning goal is to build a production-quality multi-agent system that demonstrates: supervisor routing, subgraphs, conditional edges, reflection loops, RAG with persistent memory, parallel execution, human-in-the-loop interrupts, and experiment tracking — all in one cohesive project.

## Who uses it?

A data scientist or ML engineer who wants to automate the mechanical parts of a Kaggle competition workflow: EDA, literature research, baseline establishment, hyperparameter tuning, and iterative improvement. The user stays in control at key decision points but delegates the execution to the agent team. Also serves as a reference implementation for anyone learning multi-agent system design with LangGraph.

## How does it work at a high level?

The user provides: a problem statement, a dataset path, and optionally a Kaggle competition URL. The system runs through five phases — Understanding, Research, Design, Implementation, Evaluation — then pauses and presents the results to the user. The user decides whether to accept the solution or approve another improvement iteration. Each iteration the system gets smarter: it reads its own past experiments from a RAG vector store and avoids repeating failures. A `kaggle-client` agent can optionally submit to the leaderboard. The cycle continues until the user is satisfied or a stopping criterion is met.

## Full agent architecture

### Orchestration layer

**`supervisor`**
The root node of the LangGraph StateGraph. Does no technical work. Reads the current state and routes to the correct phase or agent. Decides when a stopping criterion has been reached (N iterations without meaningful improvement) and escalates to the user. Manages human-in-the-loop interrupt points.

---

### Phase 1 — Understanding

**`data-analyst`**
Full EDA: distributions, correlations, missing values, outliers, class imbalance, cardinality of categoricals, temporal patterns if any, potential feature interactions. Executes real Python code via `code-executor`. Writes a structured EDA report into shared state.

**`problem-framer`**
Reads the competition description and EDA report. Classifies the problem type (binary classification, multiclass, regression, NLP, time series, ranking…). Defines the success metric (AUC-ROC, RMSE, F1, MAP…). Identifies key constraints (imbalanced labels, temporal structure, group structure, data size). Writes a structured problem definition into shared state.

**`validation-strategist`**
Critical decision node — runs once, result is locked for the entire experiment lifecycle. Analyzes the data structure and problem type to select the validation strategy: stratified k-fold (classification with imbalance), k-fold (regression), group k-fold (repeated entities — same user, same patient), time-series split (temporal data, no future leakage), or adversarial validation (detects train/test distribution shift). Generates and freezes the fold indices in shared state. All downstream agents inherit these exact folds — no exceptions.

**`leakage-auditor`** *(mandatory before any model runs)*
Dedicated check for data leakage. Inspects features for: future information in temporal data, target-correlated IDs, features derived from the target, train/test contamination. Writes a leakage report. If critical leakage is found, blocks progression and escalates to the user before any model is trained.

**`critic`** *(shared agent, used across phases)*
Reviews any agent's output and decides pass or iterate. Returns structured feedback. Maximum 3 retry cycles per agent to prevent infinite loops. Used after Understanding, after Design, and after each Implementation cycle.

*Human checkpoint after Phase 1: system presents EDA summary, problem framing, and validation strategy. User approves or corrects before proceeding.*

---

### Phase 2 — Research

Agents in this phase run in **parallel** (LangGraph map node).

**`literature-researcher`**
Searches arxiv and Semantic Scholar for papers relevant to the problem type and domain. Downloads abstracts and key sections. Indexes findings into the RAG vector store.

**`web-researcher`**
Searches the web (Tavily API) for blog posts, GitHub implementations, and community discussions relevant to the problem. Indexes into RAG.

**`competition-analyst`**
Uses the Kaggle API to access the competition forum, top-voted public notebooks, and discussions. Extracts: which features matter, which model families won similar competitions historically. Indexes into RAG.

**`memory-manager`**
After the parallel research phase completes: consolidates the RAG store, deduplicates entries, organizes by relevance. In subsequent iterations, answers queries like "what have we tried before that failed?" and "what does the literature say about handling this specific issue?" The persistent knowledge layer across the entire session.

---

### Phase 3 — Baseline

Runs before any complex modeling. Establishes three anchor points in order:

**`baseline-trivial`**
DummyClassifier (most frequent / stratified) or DummyRegressor (mean / median). Uses the frozen validation folds. Records score. This is the floor — anything below this is broken.

**`baseline-simple`**
Logistic Regression or Ridge/Lasso Regression with minimal preprocessing (impute nulls, encode categoricals). Uses frozen folds. Records score. Reveals whether the problem has linear signal.

**`baseline-strong`**
RandomForest or XGBoost/LightGBM with default hyperparameters. Proper preprocessing pipeline. Uses frozen folds. Records score. This is the real reference point before optimization begins.

All three results are stored in `experiments` in shared state and become permanent benchmarks. All future improvements are measured against `baseline-strong`.

*Human checkpoint after Phase 3 (optional, configurable): show baseline scores before investing in research-driven design.*

---

### Phase 4 — Design

**`solution-architect`**
Reads: EDA report, problem framing, baseline results, all RAG findings. Designs the solution strategy: which model families to pursue, in what order, what preprocessing pipeline, whether ensembling is warranted, what the realistic ceiling is. Writes a structured plan into shared state.

**`feature-engineer`**
Reads: solution plan + EDA report. Designs feature transformations: encoding strategies for categoricals, null handling, feature interactions, temporal feature extraction if applicable, target encoding with proper fold-aware implementation to prevent leakage. Does not write code — produces a feature specification that the coder implements.

*Human checkpoint after Phase 4: user reviews and approves the solution plan before implementation begins.*

---

### Phase 5 — Implementation

Specialists run in **parallel** — the supervisor activates only those relevant to the problem type (no NLP specialist for a pure tabular problem).

**`classical-ml-specialist`**
Designs experiments for: XGBoost, LightGBM, CatBoost, ExtraTrees. Specifies hyperparameter search spaces. Knows when to use each, how to handle imbalance, how to tune learning rate vs depth tradeoffs.

**`deep-learning-specialist`**
Designs experiments for neural approaches: TabNet, NODE, MLP with embeddings for categoricals, or task-specific architectures. Activated when dataset is large enough to benefit.

**`nlp-specialist`**
Activated when text features exist. Designs: TF-IDF baselines, transformer embeddings (sentence-transformers), fine-tuning if compute allows.

**`timeseries-specialist`**
Activated when temporal structure exists. Designs: lag features, rolling statistics, ARIMA/Prophet for univariate baselines, temporal CV strategy validation.

**`ensemble-specialist`**
Activated after at least two specialists have results. Designs: stacking, blending, weighted averaging. Uses out-of-fold predictions from the frozen folds to prevent leakage in the ensemble layer.

**`coder`**
The only agent that writes implementation code. Receives the feature specification + specialist design. Implements using `code-executor`. Handles execution errors, fixes them, and iterates until the code runs cleanly. Produces: a reproducible training pipeline, saved model artifacts, and out-of-fold predictions.

**`code-executor`** *(tool, not an LLM agent)*
Local Python subprocess with output capture. Executes code, returns stdout/stderr/metrics. Used by the coder and all specialists that need to run quick data checks.

**Hyperparameter tuning (inner loop — fully automatic, no user intervention)**
Each specialist's design includes a hyperparameter search space. The coder implements this with Optuna. Optuna runs N trials automatically (configurable, default 50). Only the best result from the inner loop is passed to Evaluation. The user never sees individual trials.

**Stopping criterion for the inner loop:** if the last 20 trials show no improvement beyond a threshold, Optuna stops early. This prevents infinite tuning within a single iteration.

---

### Phase 6 — Evaluation

**`evaluator`**
Receives all experiment results (CV scores, out-of-fold predictions). Compares against the strong baseline and against all previous iterations. Calculates improvement delta. Checks for CV vs leaderboard divergence if a Kaggle submission was made. Decides: improvement is significant (continue) or marginal (flag for user).

**`feature-importance-analyst`**
After each trained model: extracts feature importances (SHAP values for tree models, gradient-based for neural). Identifies: which features drive predictions, which features are near-zero and can be dropped, which interactions appear unexpectedly important. Writes findings back into the RAG store and directly into the feature-engineer's context for the next iteration.

**`error-analyst`**
When score does not meet the improvement threshold: diagnoses root cause. Categories: overfitting (train score >> CV score), underfitting (both scores low), validation strategy mismatch (CV and LB diverge), feature quality issue, wrong model family for the data structure. Writes a structured diagnosis into shared state.

**`hypothesis-generator`**
Reads the error diagnosis + all past experiments from RAG. Proposes concrete, prioritized hypotheses for the next iteration. Avoids hypotheses that were already tried and failed (RAG prevents repetition). Example: "Hypothesis 1 (high confidence): target encoding of feature X is leaking — use fold-aware encoding. Hypothesis 2 (medium confidence): LightGBM is underfitting, increase num_leaves from 31 to 128."

**`experiment-designer`**
Converts hypotheses into a concrete experiment plan: what to change, in what order, which specialists to activate, what the expected impact is. This output goes to the supervisor, which routes back to Phase 4 (partial re-run) or Phase 3 (full redesign) based on how fundamental the changes are.

*Human checkpoint after Phase 6 (mandatory): system presents full iteration report — scores, deltas, best model, diagnosis, and proposed next iteration plan. User decides: approve next iteration, modify the plan, or go to delivery.*

---

### Phase 7 — Delivery

**`reviewer`**
Reviews the final code for: reproducibility (fixed random seeds, no hardcoded paths), no data leakage in the inference pipeline, clean structure, no debug prints or dead code.

**`report-writer`**
Generates a structured run report: what was tried, what worked, what didn't, why the final solution was chosen, key findings from feature importance analysis, lessons learned for future runs.

**`kaggle-client`** *(optional, activated only if user enables submissions)*
Uses the Kaggle API to: download competition data, format the submission file, upload to the leaderboard, retrieve the public score. Compares public LB score to CV score. If divergence is detected, flags it in the report.

---

## Shared state (LabState)

```python
class LabState(TypedDict):
    # Input
    problem_statement: str
    dataset_path: str
    competition_url: str

    # Understanding
    eda_report: dict
    problem_type: str
    success_metric: str
    leakage_report: dict

    # Validation (frozen after Phase 1)
    validation_strategy: str
    fold_indices: list          # frozen, never modified after Phase 1

    # Research
    research_notes: list

    # Baselines (permanent benchmarks)
    baseline_trivial_score: float
    baseline_simple_score: float
    baseline_strong_score: float

    # Design
    solution_plan: dict
    feature_specification: dict

    # Experiments (append-only log)
    experiments: list           # [{id, model, params, cv_score, notes, iteration}]
    best_model: dict
    best_submission: dict       # sacred — only updated on explicit improvement

    # Current iteration
    current_iteration: int
    max_iterations: int         # stopping criterion
    iterations_without_improvement: int

    # Evaluation
    last_score: float
    score_delta: float
    feature_importances: dict
    error_diagnosis: str
    hypotheses: list

    # Delivery
    submission_ready: bool
    report: str

    # Control
    phase: str
    human_feedback: str
    messages: list
```

---

## RAG architecture

**Vector store**: Chroma (local, persistent across runs).

**What gets indexed**:
- Research findings (papers, web, competition forum)
- Experiment results with full context (what was tried, score, why it failed/succeeded)
- Feature importance findings
- Error diagnoses and their resolutions
- Hypotheses that were validated or invalidated

**Query pattern**: every agent that needs context queries the store before making decisions. The memory-manager maintains quality (deduplication, relevance scoring, pruning stale entries).

**Cross-run learning**: the vector store persists between different competition runs. Over time, the system accumulates knowledge about problem patterns, effective approaches, and common failure modes.

---

## LangGraph concepts demonstrated

| Concept | Where |
|---|---|
| StateGraph with complex state | LabState flowing through all nodes |
| Supervisor routing pattern | supervisor node with conditional edges |
| Subgraphs | each phase compiled as a subgraph |
| Parallel execution (map) | Research phase, Implementation specialists |
| Conditional edges | evaluation → improvement or delivery |
| Human-in-the-loop (interrupt) | after Phase 1, Phase 4, Phase 6 |
| Reflection pattern | agent → critic → agent loop |
| RAG as a tool | memory-manager + vector store |
| Long-term memory | Chroma persistence across runs |
| Agent-as-tool | critic invoked by multiple agents |
| Inner loop with stopping criterion | Optuna inside coder |
| Experiment tracking | append-only experiments log in state |

---

## Tech stack

- **Orchestration**: LangGraph (local)
- **LLM**: Claude (configurable per agent slot)
- **Vector store**: Chroma (local, persistent)
- **Hyperparameter tuning**: Optuna
- **Code execution**: local subprocess (sandbox)
- **Kaggle integration**: kaggle-api Python package (optional)
- **Observability**: LangSmith (optional, toggle via env var)
- **ML libraries**: scikit-learn, XGBoost, LightGBM, CatBoost, PyTorch (optional)

---

## What is definitely NOT part of this

- Cloud deployment or hosted infrastructure
- Multi-user support or authentication
- Real-time / streaming data
- Production serving of trained models
- AutoML as a black box — this system is transparent and explainable by design
