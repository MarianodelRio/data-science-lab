"""Unit tests for src/config/prompts.py."""

from pathlib import Path

import pytest

from src.config.errors import ConfigError
from src.config.prompts import PromptLoader

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "prompts"


def test_load_returns_file_contents() -> None:
    loader = PromptLoader(prompts_dir=FIXTURES_DIR)

    content = loader.load("x", "v1")

    expected = (FIXTURES_DIR / "x" / "v1.md").read_text(encoding="utf-8")
    assert content == expected
    assert "Prompt fixture" in content


def test_load_missing_file_raises_filenotfounderror_with_path() -> None:
    loader = PromptLoader(prompts_dir=FIXTURES_DIR)

    expected_path = FIXTURES_DIR / "does_not_exist" / "v1.md"
    with pytest.raises(FileNotFoundError) as exc_info:
        loader.load("does_not_exist", "v1")

    assert str(expected_path) in str(exc_info.value)


@pytest.mark.parametrize(
    "agent,version",
    [
        ("../../etc/passwd", "v1"),
        ("..", "v1"),
        ("a/b", "v1"),
        ("a\\b", "v1"),
        ("x", "../../etc/passwd"),
        ("x", ".."),
        ("x", "a/b"),
    ],
)
def test_load_path_traversal_raises_config_error(agent: str, version: str) -> None:
    loader = PromptLoader(prompts_dir=FIXTURES_DIR)

    with pytest.raises(ConfigError, match="[Ii]nvalid"):
        loader.load(agent, version)
