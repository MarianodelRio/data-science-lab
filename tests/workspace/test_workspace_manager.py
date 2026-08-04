"""Unit tests for WorkspaceManager — the sole file-I/O point to the ML workspace."""

from __future__ import annotations

import os
from pathlib import Path

import nbformat
import pytest

from src.workspace.workspace_manager import WorkspaceManager


@pytest.fixture
def wm(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


# --- write_json / read_json --------------------------------------------------


def test_write_json_creates_file_and_returns_absolute_path(wm: WorkspaceManager) -> None:
    result = wm.write_json("reports/eda.json", {"a": 1})
    assert isinstance(result, str)
    result_path = Path(result)
    assert result_path.is_absolute()
    assert result_path.exists()
    assert result_path.is_relative_to(wm.workspace_path)


def test_read_json_round_trips_nested_dict_exactly(wm: WorkspaceManager) -> None:
    data = {
        "int": 1,
        "float": 3.14,
        "str": "hello",
        "bool": True,
        "none": None,
        "list": [1, 2, {"nested": "dict"}],
        "nested": {"a": {"b": [None, False, 2.5]}},
    }
    wm.write_json("nested.json", data)
    assert wm.read_json("nested.json") == data


def test_write_json_auto_creates_missing_parent_dirs(wm: WorkspaceManager) -> None:
    wm.write_json("deeply/nested/dir/file.json", {"x": 1})
    assert (wm.workspace_path / "deeply" / "nested" / "dir" / "file.json").exists()


def test_read_json_missing_file_raises_file_not_found(wm: WorkspaceManager) -> None:
    with pytest.raises(FileNotFoundError):
        wm.read_json("does/not/exist.json")


def test_write_json_overwrites_not_appends(wm: WorkspaceManager) -> None:
    wm.write_json("file.json", {"v": 1})
    wm.write_json("file.json", {"v": 2})
    assert wm.read_json("file.json") == {"v": 2}


# --- write_text / read_text --------------------------------------------------


def test_write_read_text_round_trips_multiline_unicode(wm: WorkspaceManager) -> None:
    content = "line one\nlíne twö — 日本語\nthird line\n"
    wm.write_text("notes/readme.txt", content)
    assert wm.read_text("notes/readme.txt") == content


def test_write_text_returns_absolute_path_string(wm: WorkspaceManager) -> None:
    result = wm.write_text("a.txt", "hi")
    assert isinstance(result, str)
    assert Path(result).is_absolute()


def test_write_text_auto_creates_missing_parent_dirs(wm: WorkspaceManager) -> None:
    wm.write_text("deeply/nested/dir/file.txt", "content")
    assert (wm.workspace_path / "deeply" / "nested" / "dir" / "file.txt").exists()


# --- path safety: absolute paths ---------------------------------------------


@pytest.mark.parametrize(
    "method_name",
    [
        "read_json",
        "write_json",
        "read_text",
        "write_text",
        "write_notebook",
        "ensure_dir",
        # NOTE: experiment_dir is intentionally excluded here. It builds its path via
        # f"experiments/{exp_id}", so an absolute exp_id (e.g. "/etc/passwd") is string-
        # concatenated after "experiments/" and Path() normalizes the result to a *relative*
        # path ("experiments/etc/passwd") rather than raising ValueError. Traversal via ".."
        # still survives concatenation as a path component and correctly raises (covered by
        # test_traversal_path_raises_value_error). This matches the approved implementation
        # verbatim; see context/decisions.md for the T-005 entry on this asymmetry.
    ],
)
def test_absolute_path_raises_value_error(wm: WorkspaceManager, method_name: str) -> None:
    method = getattr(wm, method_name)
    absolute = "/etc/passwd"
    with pytest.raises(ValueError):
        _call_with_path_arg(method, method_name, absolute)


def test_experiment_dir_absolute_looking_exp_id_is_not_rejected(
    wm: WorkspaceManager,
) -> None:
    # Documents the known asymmetry above: experiment_dir does not treat an absolute-looking
    # exp_id as an absolute path, since it is concatenated after the "experiments/" prefix.
    result = wm.experiment_dir("/etc/passwd")
    assert result == wm.workspace_path / "experiments" / "etc" / "passwd"


@pytest.mark.parametrize(
    "method_name",
    [
        "read_json",
        "write_json",
        "read_text",
        "write_text",
        "write_notebook",
        "ensure_dir",
        "experiment_dir",
    ],
)
@pytest.mark.parametrize("traversal", ["../escape.txt", "a/../../b.txt", ".."])
def test_traversal_path_raises_value_error(
    wm: WorkspaceManager, method_name: str, traversal: str
) -> None:
    method = getattr(wm, method_name)
    with pytest.raises(ValueError):
        _call_with_path_arg(method, method_name, traversal)


def _call_with_path_arg(method, method_name: str, path_value: str) -> None:
    """Invoke `method` with `path_value` as its sole relative-path-like argument."""
    if method_name == "write_json":
        method(path_value, {"a": 1})
    elif method_name == "write_text":
        method(path_value, "content")
    elif method_name == "write_notebook":
        method(path_value, [])
    else:
        method(path_value)


@pytest.mark.parametrize(
    "method_name",
    [
        "read_json",
        "write_json",
        "read_text",
        "write_text",
        "write_notebook",
        "ensure_dir",
        # NOTE: experiment_dir is intentionally excluded here too. "" / "." only reach the
        # empty-path check in _resolve when they *are* the full relative_path; experiment_dir
        # always prefixes with "experiments/" first, so an empty/"." exp_id normalizes to the
        # harmless "experiments" directory itself, not the workspace root — no bug, nothing to
        # reject.
    ],
)
@pytest.mark.parametrize("empty_like", ["", "."])
def test_empty_or_dot_relative_path_raises_value_error(
    wm: WorkspaceManager, method_name: str, empty_like: str
) -> None:
    method = getattr(wm, method_name)
    with pytest.raises(ValueError):
        _call_with_path_arg(method, method_name, empty_like)


def test_literal_dotdot_substring_in_filename_does_not_raise(wm: WorkspaceManager) -> None:
    # "report..v2.json" contains ".." as a substring but is a single path component,
    # not a traversal component — must NOT be rejected.
    result = wm.write_json("report..v2.json", {"ok": True})
    assert Path(result).exists()
    assert wm.read_json("report..v2.json") == {"ok": True}


# --- experiment_dir / ensure_dir ---------------------------------------------


def test_experiment_dir_computes_path_without_creating_it(wm: WorkspaceManager) -> None:
    result = wm.experiment_dir("baseline")
    assert result == wm.workspace_path / "experiments" / "baseline"
    assert not result.exists()


def test_experiment_dir_traversal_raises_value_error(wm: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        wm.experiment_dir("../escape")


def test_ensure_dir_creates_and_returns_path(wm: WorkspaceManager) -> None:
    result = wm.ensure_dir("data/processed")
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_dir()
    assert result == wm.workspace_path / "data" / "processed"


def test_ensure_dir_is_idempotent_and_preserves_contents(wm: WorkspaceManager) -> None:
    first = wm.ensure_dir("data/processed")
    marker = first / "marker.txt"
    marker.write_text("keep me")

    second = wm.ensure_dir("data/processed")

    assert second == first
    assert marker.exists()
    assert marker.read_text() == "keep me"


# --- write_notebook ------------------------------------------------------------


def test_write_notebook_with_code_and_markdown_cells_is_loadable(wm: WorkspaceManager) -> None:
    cells = [
        {"cell_type": "markdown", "source": "# Title"},
        {"cell_type": "code", "source": "print('hello')"},
        {"cell_type": "markdown", "source": "Some notes."},
    ]
    result = wm.write_notebook("notebooks/eda.ipynb", cells)

    assert isinstance(result, str)
    nb = nbformat.read(result, as_version=4)
    assert len(nb["cells"]) == 3
    assert nb["cells"][0]["cell_type"] == "markdown"
    assert nb["cells"][0]["source"] == "# Title"
    assert nb["cells"][1]["cell_type"] == "code"
    assert nb["cells"][1]["source"] == "print('hello')"
    assert nb["cells"][2]["cell_type"] == "markdown"
    assert nb["cells"][2]["source"] == "Some notes."


def test_write_notebook_invalid_cell_type_raises_value_error(wm: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        wm.write_notebook("bad.ipynb", [{"cell_type": "raw", "source": "x"}])


def test_write_notebook_invalid_cell_type_error_names_valid_options(
    wm: WorkspaceManager,
) -> None:
    with pytest.raises(ValueError, match="expected 'code' or 'markdown'"):
        wm.write_notebook("bad.ipynb", [{"cell_type": "raw", "source": "x"}])


def test_write_notebook_non_dict_cell_raises_value_error(wm: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        wm.write_notebook("bad.ipynb", ["not a dict"])  # type: ignore[list-item]


def test_write_notebook_non_string_source_raises_value_error(wm: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        wm.write_notebook("bad.ipynb", [{"cell_type": "code", "source": 12345}])


def test_write_notebook_empty_cells_produces_loadable_empty_notebook(
    wm: WorkspaceManager,
) -> None:
    result = wm.write_notebook("empty.ipynb", [])
    nb = nbformat.read(result, as_version=4)
    assert nb["cells"] == []


def test_write_notebook_calls_nbformat_validate(
    wm: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Locks in that nbformat.validate() is actually invoked (a mutation deleting that call
    # previously survived, since no prior test's input was rejected *only* by validate()).
    calls: list = []
    original_validate = nbformat.validate

    def spy_validate(nb: object, *args: object, **kwargs: object) -> None:
        calls.append(nb)
        original_validate(nb, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(nbformat, "validate", spy_validate)

    wm.write_notebook("spy.ipynb", [{"cell_type": "code", "source": "1 + 1"}])

    # nbformat.validate() recurses internally (it validates nested cell structures via the
    # same module-level function we patched), so assert it fired at least once rather than
    # pinning an exact call count.
    assert len(calls) >= 1


# --- constructor ---------------------------------------------------------------


def test_constructor_creates_workspace_root_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "new_root"
    assert not root.exists()
    wm = WorkspaceManager(root)
    assert root.exists()
    assert wm.workspace_path == root.resolve()


# --- return type checks ---------------------------------------------------------


def test_write_methods_return_str_and_dir_methods_return_path(wm: WorkspaceManager) -> None:
    assert isinstance(wm.write_json("t1.json", {}), str)
    assert isinstance(wm.write_text("t2.txt", ""), str)
    assert isinstance(wm.write_notebook("t3.ipynb", []), str)
    assert isinstance(wm.ensure_dir("t4dir"), Path)
    assert isinstance(wm.experiment_dir("exp1"), Path)


# --- atomic writes: original content preserved on failed write -----------------


class _Unserializable:
    """Deliberately not JSON-serializable, to trigger a genuine mid-write failure."""


def test_write_json_preserves_old_content_on_serialization_failure(
    wm: WorkspaceManager,
) -> None:
    wm.write_json("data.json", {"good": "content"})

    with pytest.raises(TypeError):
        wm.write_json("data.json", {"bad": _Unserializable()})

    assert wm.read_json("data.json") == {"good": "content"}
    # no leftover temp file in the target directory
    leftovers = [p for p in wm.workspace_path.iterdir() if p.name != "data.json"]
    assert leftovers == []


def test_write_text_preserves_old_content_on_failed_write(
    wm: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    wm.write_text("notes.txt", "original content")

    def boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        wm.write_text("notes.txt", "new content")
    monkeypatch.undo()

    assert wm.read_text("notes.txt") == "original content"
    leftovers = [p for p in wm.workspace_path.iterdir() if p.name != "notes.txt"]
    assert leftovers == []


def test_write_notebook_preserves_old_content_on_failed_write(
    wm: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    wm.write_notebook("nb.ipynb", [{"cell_type": "code", "source": "1 + 1"}])
    original_bytes = (wm.workspace_path / "nb.ipynb").read_bytes()

    def boom(nb: object, path: object, *args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(nbformat, "write", boom)
    with pytest.raises(OSError):
        wm.write_notebook("nb.ipynb", [{"cell_type": "code", "source": "2 + 2"}])
    monkeypatch.undo()

    assert (wm.workspace_path / "nb.ipynb").read_bytes() == original_bytes
    leftovers = [p for p in wm.workspace_path.iterdir() if p.name != "nb.ipynb"]
    assert leftovers == []


def test_write_json_atomic_replace_is_used(
    wm: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Locks in that the swap goes through os.replace (the atomic rename), not some other
    # non-atomic copy mechanism.
    calls: list = []
    original_replace = os.replace

    def spy_replace(src: object, dst: object) -> None:
        calls.append((src, dst))
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    wm.write_json("atomic.json", {"a": 1})

    assert len(calls) == 1


# --- empty relative_path edge cases ---------------------------------------------


def test_empty_relative_path_does_not_touch_workspace_root(wm: WorkspaceManager) -> None:
    # Guards the fix: _resolve("") used to silently return the workspace root itself,
    # leaking a raw IsADirectoryError from read/write calls instead of ValueError.
    with pytest.raises(ValueError):
        wm.read_json("")
    assert wm.workspace_path.exists()  # root itself untouched/still a directory
