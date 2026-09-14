"""Integration test: the API must fail to start (not merely fail some later
request) when a required provider/Kaggle API key is missing or empty.

Uses the real `Settings.validate_required_keys` — not a fake — injected via
`create_app(key_validator=...)`, against a real `tmp_path` settings.yaml.
`validate_required_keys` only requires the `api_keys` section to be present
(`src/config/settings.py:307-330`), so the stub YAML below needs only that
section, not `models`/`context`/`workspace`/`optuna`/`execution`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.config.errors import ConfigError
from src.config.settings import Settings

_COMPLETE_API_KEYS = {
    "anthropic": "sk-ant-real-key",
    "deepseek": "sk-deepseek-real-key",
    "groq": "sk-groq-real-key",
    "kaggle_username": "real-kaggle-user",
    "kaggle_key": "real-kaggle-key",
}


def _write_settings(tmp_path: Path, api_keys: dict[str, str | None]) -> Path:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump({"api_keys": api_keys}), encoding="utf-8")
    return settings_path


def test_startup_fails_when_required_api_key_is_missing(tmp_path: Path) -> None:
    incomplete_api_keys = {**_COMPLETE_API_KEYS, "deepseek": ""}
    settings_path = _write_settings(tmp_path, incomplete_api_keys)
    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: None,
        key_validator=lambda: Settings.validate_required_keys(path=settings_path),
    )

    with pytest.raises(ConfigError, match="deepseek"), TestClient(app):
        pass


def test_startup_succeeds_when_all_required_api_keys_present(tmp_path: Path) -> None:
    settings_path = _write_settings(tmp_path, _COMPLETE_API_KEYS)
    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: None,
        key_validator=lambda: Settings.validate_required_keys(path=settings_path),
    )

    with TestClient(app) as client:
        response = client.get("/api/mlflow/url")

    assert response.status_code == 200
