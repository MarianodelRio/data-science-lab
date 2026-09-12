"""Unit tests for `src/api/registry.py` — the on-disk run registry.

No FastAPI or graph dependency: these tests exercise the registry module in
isolation, per the Planner's implementation order.
"""

from pathlib import Path

import pytest

from src.api.registry import (
    RunNotFoundError,
    create_run_record,
    list_run_records,
    read_run_record,
    update_run_status,
    validate_run_id,
)


def test_validate_run_id_accepts_normal_uuid_hex() -> None:
    validate_run_id("abc123")  # must not raise


@pytest.mark.parametrize("bad_id", ["", ".", "..", "a/b", "a\\b", "..evil", "foo/../bar"])
def test_validate_run_id_rejects_unsafe_values(bad_id: str) -> None:
    with pytest.raises(ValueError):
        validate_run_id(bad_id)


def test_create_run_record_writes_pending_status_and_readable_file(tmp_path: Path) -> None:
    record = create_run_record(tmp_path, "run-1", "titanic", "/workspace/titanic")

    assert record.status == "pending"
    assert record.run_id == "run-1"
    assert record.competition_name == "titanic"
    assert record.workspace_path == "/workspace/titanic"
    assert (tmp_path / "run-1" / "run.json").exists()


def test_read_run_record_returns_persisted_fields(tmp_path: Path) -> None:
    create_run_record(tmp_path, "run-1", "titanic", "/workspace/titanic")

    record = read_run_record(tmp_path, "run-1")

    assert record.run_id == "run-1"
    assert record.competition_name == "titanic"


def test_read_run_record_missing_run_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(RunNotFoundError):
        read_run_record(tmp_path, "does-not-exist")


def test_read_run_record_invalid_run_id_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(RunNotFoundError):
        read_run_record(tmp_path, "../escape")


def test_update_run_status_persists_new_status_and_bumps_updated_at(tmp_path: Path) -> None:
    original = create_run_record(tmp_path, "run-1", "titanic", "/workspace/titanic")

    updated = update_run_status(tmp_path, "run-1", "running")

    assert updated.status == "running"
    assert updated.updated_at >= original.updated_at
    reread = read_run_record(tmp_path, "run-1")
    assert reread.status == "running"


def test_list_run_records_returns_empty_list_when_runs_dir_missing(tmp_path: Path) -> None:
    missing_dir = tmp_path / "does-not-exist"

    assert list_run_records(missing_dir) == []


def test_list_run_records_returns_all_created_runs(tmp_path: Path) -> None:
    create_run_record(tmp_path, "run-1", "titanic", "/workspace/titanic")
    create_run_record(tmp_path, "run-2", "spaceship", "/workspace/spaceship")

    records = list_run_records(tmp_path)

    assert {r.run_id for r in records} == {"run-1", "run-2"}


def test_list_run_records_skips_unparseable_run_json(tmp_path: Path) -> None:
    create_run_record(tmp_path, "run-good", "titanic", "/workspace/titanic")
    bad_dir = tmp_path / "run-bad"
    bad_dir.mkdir()
    (bad_dir / "run.json").write_text("not valid json{{{", encoding="utf-8")

    records = list_run_records(tmp_path)

    assert [r.run_id for r in records] == ["run-good"]
