"""Unit tests for `src/api/routers/mlflow.py`. Zero network calls."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.config.settings import Settings


def test_get_mlflow_url_returns_configured_url(client: TestClient) -> None:
    response = client.get("/api/mlflow/url")

    assert response.status_code == 200
    assert response.json() == {"url": "http://mlflow.example.test:5000"}


def test_get_mlflow_url_defaults_to_localhost_5000_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MLFLOW_PUBLIC_URL", raising=False)
    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: None,
        mlflow_url=None,
        key_validator=lambda: None,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/mlflow/url")

    assert response.json()["url"] == "http://localhost:5000"


def test_get_mlflow_url_reads_env_var_when_param_not_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_PUBLIC_URL", "http://mlflow.example.com:5000")
    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: None,
        mlflow_url=None,
        key_validator=lambda: None,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/mlflow/url")

    assert response.json()["url"] == "http://mlflow.example.com:5000"


def test_get_mlflow_url_param_takes_precedence_over_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_PUBLIC_URL", "http://env-value.example.com:5000")
    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: None,
        mlflow_url="http://param-value.example.com:5000",
        key_validator=lambda: None,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/mlflow/url")

    assert response.json()["url"] == "http://param-value.example.com:5000"


def test_get_mlflow_url_never_calls_settings_load(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("Settings.load() must not be called by GET /api/mlflow/url")

    monkeypatch.setattr(Settings, "load", staticmethod(_fail_if_called))

    response = client.get("/api/mlflow/url")

    assert response.status_code == 200
