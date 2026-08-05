"""Contract tests for the 7 real `config/phases/*.yaml` files.

Loads each file through the real `load_phase_config` (no `base_dir` override —
these are the actual files GraphBuilder reads) and spot-checks the literal
content against design.md's § The seven phases.
"""

import pytest

from src.config.loaders import load_phase_config
from src.config.schema import CriticConfig, PhaseConfig

EXPECTED: dict[str, dict] = {
    "phase1_understanding": {
        "name": "understanding",
        "nodes": (
            "data_analyst",
            "problem_framer",
            "validation_strategist",
            "leakage_auditor",
            "analysis_critic",
        ),
        "parallel_nodes": (),
        "critic": CriticConfig(
            node="analysis_critic",
            targets=("data_analyst", "problem_framer", "validation_strategist", "leakage_auditor"),
            max_retries=3,
        ),
        "interrupt_after": True,
    },
    "phase2_research": {
        "name": "research",
        "nodes": (
            "literature_researcher",
            "web_researcher",
            "competition_analyst",
            "memory_manager",
        ),
        "parallel_nodes": ("literature_researcher", "web_researcher"),
        "critic": None,
        "interrupt_after": False,
    },
    "phase3_baseline": {
        "name": "baseline",
        "nodes": ("baseline_designer", "baseline_runner"),
        "parallel_nodes": (),
        "critic": None,
        "interrupt_after": False,
    },
    "phase4_design": {
        "name": "design",
        "nodes": ("solution_architect", "feature_engineer", "analysis_critic"),
        "parallel_nodes": (),
        "critic": CriticConfig(
            node="analysis_critic",
            targets=("solution_architect", "feature_engineer"),
            max_retries=3,
        ),
        "interrupt_after": True,
    },
    "phase5_implementation": {
        "name": "implementation",
        "nodes": (
            "specialist_selector",
            "classical_ml_specialist",
            "deep_learning_specialist",
            "nlp_specialist",
            "timeseries_specialist",
            "ensemble_specialist",
            "coder",
            "code_critic",
        ),
        "parallel_nodes": (),
        "critic": CriticConfig(node="code_critic", targets=("coder",), max_retries=3),
        "interrupt_after": False,
    },
    "phase6_evaluation": {
        "name": "evaluation",
        "nodes": (
            "score_evaluator",
            "feature_importance_extractor",
            "error_analyst",
            "hypothesis_generator",
            "experiment_designer",
        ),
        "parallel_nodes": (),
        "critic": None,
        "interrupt_after": True,
    },
    "phase7_delivery": {
        "name": "delivery",
        "nodes": ("reviewer", "report_writer", "kaggle_client"),
        "parallel_nodes": (),
        "critic": None,
        "interrupt_after": False,
    },
}


@pytest.mark.parametrize("stem", sorted(EXPECTED))
def test_phase_yaml_loads_without_error(stem: str) -> None:
    config = load_phase_config(stem)
    assert isinstance(config, PhaseConfig)


@pytest.mark.parametrize("stem", sorted(EXPECTED))
def test_phase_yaml_matches_design_doc(stem: str) -> None:
    config = load_phase_config(stem)
    expected = EXPECTED[stem]

    assert config.name == expected["name"]
    assert config.nodes == expected["nodes"]
    assert config.sequence == expected["nodes"]  # every phase here is sequenced same as listed
    assert config.parallel_nodes == expected["parallel_nodes"]
    assert config.critic == expected["critic"]
    assert config.interrupt_after == expected["interrupt_after"]


def test_interrupt_after_phases_match_design_doc() -> None:
    interrupting = {stem for stem, expected in EXPECTED.items() if expected["interrupt_after"]}
    assert interrupting == {"phase1_understanding", "phase4_design", "phase6_evaluation"}
