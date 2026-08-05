"""Unit tests for src/tools/kaggle_client.py.

All tests either inject a mocked API object (no real `kaggle` import/network
call happens) or exercise `_default_api()` with `KAGGLE_CONFIG_DIR` pointed at
an empty tmp directory so it cannot fall back to a real `~/.kaggle/kaggle.json`
and cannot reach the network.
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.tools.kaggle_client import _default_api, download, get_score, submit


def _fake_submission(public_score: str, date: datetime) -> MagicMock:
    submission = MagicMock()
    submission.public_score = public_score
    submission.date = date
    return submission


def _write_zip_side_effect(members: dict[str, str]):
    def _side_effect(
        competition_arg: str, path: str, force: bool = False, quiet: bool = True
    ) -> None:
        archive_path = Path(path) / f"{competition_arg}.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)

    return _side_effect


def test_download_calls_api_with_correct_args(tmp_path: Path) -> None:
    dest_dir = tmp_path / "data"
    api = MagicMock()
    api.competition_download_files.side_effect = _write_zip_side_effect(
        {"train.csv": "a,b\n1,2\n", "test.csv": "a,b\n3,4\n"}
    )

    download("titanic", str(dest_dir), api=api)

    api.competition_download_files.assert_called_once_with(
        "titanic", path=str(dest_dir), force=True, quiet=True
    )


def test_download_returns_extracted_file_paths(tmp_path: Path) -> None:
    dest_dir = tmp_path / "data"
    api = MagicMock()
    api.competition_download_files.side_effect = _write_zip_side_effect(
        {"train.csv": "a,b\n1,2\n", "test.csv": "a,b\n3,4\n"}
    )

    result = download("titanic", str(dest_dir), api=api)

    assert sorted(result) == sorted([str(dest_dir / "train.csv"), str(dest_dir / "test.csv")])
    assert (dest_dir / "train.csv").read_text() == "a,b\n1,2\n"
    assert (dest_dir / "test.csv").read_text() == "a,b\n3,4\n"


def test_download_removes_archive_after_extraction(tmp_path: Path) -> None:
    dest_dir = tmp_path / "data"
    api = MagicMock()
    api.competition_download_files.side_effect = _write_zip_side_effect({"train.csv": "x"})

    download("titanic", str(dest_dir), api=api)

    assert not (dest_dir / "titanic.zip").exists()


def test_download_creates_dest_dir_if_missing(tmp_path: Path) -> None:
    dest_dir = tmp_path / "nested" / "does" / "not" / "exist"
    assert not dest_dir.exists()
    api = MagicMock()
    api.competition_download_files.side_effect = _write_zip_side_effect({"train.csv": "x"})

    download("titanic", str(dest_dir), api=api)

    assert dest_dir.exists()


def test_submit_passes_args_and_returns_none() -> None:
    api = MagicMock()

    result = submit("titanic", "/tmp/preds.csv", "my submission message", api=api)

    api.competition_submit.assert_called_once_with(
        file_name="/tmp/preds.csv",
        message="my submission message",
        competition="titanic",
        quiet=True,
    )
    assert result is None


def test_get_score_selects_latest_by_date_not_list_order() -> None:
    api = MagicMock()
    earlier = _fake_submission("0.70", datetime(2024, 1, 1))
    later = _fake_submission("0.85", datetime(2024, 6, 1))
    # Later-dated submission first in the list, earlier-dated last — proves
    # selection is by max(date), not by index/order.
    api.competition_submissions.return_value = [later, earlier]

    result = get_score("titanic", api=api)

    assert result == {"public_score": 0.85, "submitted_at": later.date.isoformat()}


def test_get_score_raises_runtime_error_when_no_submissions() -> None:
    api = MagicMock()
    api.competition_submissions.return_value = []

    with pytest.raises(RuntimeError, match="titanic"):
        get_score("titanic", api=api)


def test_missing_kaggle_username_raises_before_any_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="KAGGLE_USERNAME"):
        download("titanic", "/tmp/wherever")

    with pytest.raises(RuntimeError, match="KAGGLE_USERNAME"):
        submit("titanic", "/tmp/preds.csv", "msg")

    with pytest.raises(RuntimeError, match="KAGGLE_USERNAME"):
        get_score("titanic")


def test_missing_kaggle_key_only_raises_naming_kaggle_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAGGLE_USERNAME", "someuser")
    monkeypatch.delenv("KAGGLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="KAGGLE_KEY"):
        download("titanic", "/tmp/wherever")


def test_default_api_lazy_import_and_authenticate_succeeds_with_fake_creds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises `_default_api()` for real (fake env creds, empty
    `KAGGLE_CONFIG_DIR` so no real `~/.kaggle/kaggle.json` can be read) and
    checks the returned object exposes the expected methods. `authenticate()`
    in this installed version only reads env vars / a config file — it makes
    no network call.
    """
    monkeypatch.setenv("KAGGLE_USERNAME", "fake")
    monkeypatch.setenv("KAGGLE_KEY", "fake")
    monkeypatch.setenv("KAGGLE_CONFIG_DIR", str(tmp_path))

    api = _default_api()

    assert hasattr(api, "authenticate")
    assert hasattr(api, "competition_download_files")
    assert hasattr(api, "competition_submit")
    assert hasattr(api, "competition_submissions")
