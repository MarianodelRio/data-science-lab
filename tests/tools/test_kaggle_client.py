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

from src.tools.kaggle_client import _default_api, download, get_score, list_top_kernels, submit


def _fake_submission(public_score: str, date: datetime) -> MagicMock:
    submission = MagicMock()
    submission.public_score = public_score
    submission.date = date
    return submission


def _fake_kernel(ref: str, title: str, author: str, total_votes: int) -> MagicMock:
    kernel = MagicMock()
    kernel.ref = ref
    kernel.title = title
    kernel.author = author
    kernel.total_votes = total_votes
    return kernel


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


@pytest.mark.parametrize("bad_competition", ["/etc/passwd", "../evil", "../../etc/passwd"])
def test_download_rejects_traversal_or_absolute_competition(
    tmp_path: Path, bad_competition: str
) -> None:
    dest_dir = tmp_path / "data"
    api = MagicMock()

    with pytest.raises(ValueError, match="Invalid competition slug"):
        download(bad_competition, str(dest_dir), api=api)

    api.competition_download_files.assert_not_called()


def test_download_accepts_normal_slug(tmp_path: Path) -> None:
    dest_dir = tmp_path / "data"
    api = MagicMock()
    api.competition_download_files.side_effect = _write_zip_side_effect({"train.csv": "x"})

    result = download("house-prices-advanced-regression-techniques", str(dest_dir), api=api)

    assert result == [str(dest_dir / "train.csv")]


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
    # Earlier-dated submission first in the list, later-dated last — proves
    # selection is by max(date), not by index/order (index 0 would be wrong
    # here, unlike a fixture where the correct answer happens to sit first).
    api.competition_submissions.return_value = [earlier, later]

    result = get_score("titanic", api=api)

    assert result == {"public_score": 0.85, "submitted_at": later.date.isoformat()}


def test_get_score_raises_runtime_error_when_no_submissions() -> None:
    api = MagicMock()
    api.competition_submissions.return_value = []

    with pytest.raises(RuntimeError, match="titanic"):
        get_score("titanic", api=api)


def test_list_top_kernels_calls_api_with_sort_by_and_page_size() -> None:
    api = MagicMock()
    api.kernels_list.return_value = [
        _fake_kernel(f"user/k{i}", f"Kernel {i}", "user", i) for i in range(5)
    ]

    list_top_kernels("titanic", n=5, api=api)

    api.kernels_list.assert_called_once_with(
        competition="titanic", sort_by="voteCount", page_size=5
    )


def test_list_top_kernels_returns_expected_dict_shape() -> None:
    api = MagicMock()
    api.kernels_list.return_value = [
        _fake_kernel("user/great-notebook", "Great Notebook", "user", 42)
    ]

    result = list_top_kernels("titanic", n=1, api=api)

    assert result == [
        {
            "ref": "user/great-notebook",
            "title": "Great Notebook",
            "author": "user",
            "total_votes": 42,
            "url": "https://www.kaggle.com/code/user/great-notebook",
        }
    ]


def test_list_top_kernels_truncates_to_n_when_api_returns_more() -> None:
    api = MagicMock()
    api.kernels_list.return_value = [
        _fake_kernel(f"user/k{i}", f"Kernel {i}", "user", i) for i in range(20)
    ]

    result = list_top_kernels("titanic", n=3, api=api)

    assert len(result) == 3


def test_list_top_kernels_clamps_page_size_at_100_when_n_greater_than_100() -> None:
    api = MagicMock()
    api.kernels_list.return_value = [
        _fake_kernel(f"user/k{i}", f"Kernel {i}", "user", i) for i in range(100)
    ]

    list_top_kernels("titanic", n=500, api=api)

    api.kernels_list.assert_called_once_with(
        competition="titanic", sort_by="voteCount", page_size=100
    )


def test_list_top_kernels_rejects_non_positive_n_without_calling_api() -> None:
    api = MagicMock()

    with pytest.raises(ValueError, match="n must be positive"):
        list_top_kernels("titanic", n=0, api=api)

    with pytest.raises(ValueError, match="n must be positive"):
        list_top_kernels("titanic", n=-1, api=api)

    api.kernels_list.assert_not_called()


@pytest.mark.parametrize("bad_competition", ["/etc/passwd", "../evil", "../../etc/passwd"])
def test_list_top_kernels_rejects_traversal_or_absolute_competition(bad_competition: str) -> None:
    api = MagicMock()

    with pytest.raises(ValueError, match="Invalid competition slug"):
        list_top_kernels(bad_competition, api=api)

    api.kernels_list.assert_not_called()


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

    with pytest.raises(RuntimeError, match="KAGGLE_USERNAME"):
        list_top_kernels("titanic")


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
    assert hasattr(api, "kernels_list")
