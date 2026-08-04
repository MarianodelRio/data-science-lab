"""Loads agent system prompts from config/prompts/{agent}/{version}.md."""

from pathlib import Path

from src.config.paths import PROMPTS_DIR


class PromptLoader:
    def __init__(self, prompts_dir: str | Path = PROMPTS_DIR) -> None:
        self._prompts_dir = Path(prompts_dir)

    def load(self, agent: str, version: str) -> str:
        """Return the contents of {prompts_dir}/{agent}/{version}.md.

        A missing file lets `Path.read_text()`'s native `FileNotFoundError`
        propagate as-is — its message/args already contain the path.
        """
        path = self._prompts_dir / agent / f"{version}.md"
        return path.read_text()
