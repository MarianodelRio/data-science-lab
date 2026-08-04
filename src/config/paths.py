"""Canonical filesystem locations for config files.

`Path(__file__)` is `src/config/paths.py`:
- `.parents[0]` -> `src/config`
- `.parents[1]` -> `src`
- `.parents[2]` -> repo root
"""

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
CONFIG_DIR: Path = REPO_ROOT / "config"
SETTINGS_PATH: Path = CONFIG_DIR / "settings.yaml"
AGENTS_DIR: Path = CONFIG_DIR / "agents"
PHASES_DIR: Path = CONFIG_DIR / "phases"
PROMPTS_DIR: Path = CONFIG_DIR / "prompts"
