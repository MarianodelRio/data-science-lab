"""Phase subgraph package.

`PHASE_ORDER` is the fixed build/wiring order `GraphBuilder` iterates over —
matches the 7 `config/phases/*.yaml` filenames (without extension) and
design.md's § LangGraph topology main-graph sequence.
"""

PHASE_ORDER: tuple[str, ...] = (
    "phase1_understanding",
    "phase2_research",
    "phase3_baseline",
    "phase4_design",
    "phase5_implementation",
    "phase6_evaluation",
    "phase7_delivery",
)
