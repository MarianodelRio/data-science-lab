"""Unit tests for WorkspaceManager — the sole file-I/O point to the ML workspace."""

from __future__ import annotations

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


def test_write_notebook_empty_cells_produces_loadable_empty_notebook(
    wm: WorkspaceManager,
) -> None:
    result = wm.write_notebook("empty.ipynb", [])
    nb = nbformat.read(result, as_version=4)
    assert nb["cells"] == []


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
