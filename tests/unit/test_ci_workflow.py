"""Static-shape checks for the CI workflow definition.

These tests do not execute the workflow (that would require a real GitHub
Actions runner) — they assert the YAML has the structure the task requires:
correct triggers, the expected job set, and job steps that source their
commands from devteam.config.yml rather than duplicating them.
"""

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEVTEAM_CONFIG_PATH = REPO_ROOT / "devteam.config.yml"


def _load_workflow() -> dict[str, Any]:
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _job_run_commands(job: dict[str, Any]) -> str:
    """Concatenate every `run:` value in a job's steps into one string."""
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_ci_workflow_file_exists() -> None:
    assert WORKFLOW_PATH.is_file()


def test_ci_workflow_parses_as_yaml() -> None:
    doc = _load_workflow()
    assert isinstance(doc, dict)
    assert doc


def test_ci_workflow_triggers_on_pull_request_and_push_to_main() -> None:
    doc = _load_workflow()
    triggers = doc["on"]
    assert "pull_request" in triggers
    assert "push" in triggers
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["push"]["branches"] == ["main"]


def test_ci_workflow_declares_expected_jobs() -> None:
    doc = _load_workflow()
    assert set(doc["jobs"].keys()) == {"test", "lint", "type_check", "frontend"}


def test_ci_workflow_frontend_job_runs_expected_commands() -> None:
    doc = _load_workflow()
    commands = _job_run_commands(doc["jobs"]["frontend"])
    assert "npm ci" in commands
    assert "npm run lint" in commands
    assert "npm run build" in commands


def test_ci_workflow_python_jobs_use_devteam_config_commands() -> None:
    with open(DEVTEAM_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    commands = config["commands"]

    doc = _load_workflow()
    jobs = doc["jobs"]

    assert commands["test"] in _job_run_commands(jobs["test"])
    assert commands["lint"] in _job_run_commands(jobs["lint"])
    assert commands["type_check"] in _job_run_commands(jobs["type_check"])
