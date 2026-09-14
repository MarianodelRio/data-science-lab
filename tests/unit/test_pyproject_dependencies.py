"""Static checks against pyproject.toml's declared [project.dependencies].

These tests parse the project's own dependency list — they never introspect
installed-package metadata — so they discriminate a *direct* dependency from
one that merely arrives transitively via another package.
"""

import re
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib as _toml
else:
    import tomli as _toml  # already present transitively via coverage<7 on py<3.11

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

_DEPENDENCY_NAME_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)")


def _load_pyproject() -> dict[str, Any]:
    with open(PYPROJECT_PATH, "rb") as f:
        return _toml.load(f)


def _dependency_names(dependencies: list[str]) -> set[str]:
    names = set()
    for dep in dependencies:
        match = _DEPENDENCY_NAME_PATTERN.match(dep)
        if match:
            names.add(match.group(1).lower())
    return names


def _find_dependency_spec(dependencies: list[str], name: str) -> str:
    for dep in dependencies:
        match = _DEPENDENCY_NAME_PATTERN.match(dep)
        if match and match.group(1).lower() == name.lower():
            return dep
    raise AssertionError(f"dependency {name!r} not declared in pyproject.toml")


def test_pyproject_declares_torch_as_direct_dependency() -> None:
    dependencies = _load_pyproject()["project"]["dependencies"]

    assert "torch" in _dependency_names(dependencies)


def test_pyproject_torch_dependency_has_permissive_lower_bound() -> None:
    dependencies = _load_pyproject()["project"]["dependencies"]

    spec = _find_dependency_spec(dependencies, "torch")

    assert ">=" in spec
    assert "==" not in spec
