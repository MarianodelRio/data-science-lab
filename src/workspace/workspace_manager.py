"""Sole file-I/O point to the generated ML workspace.

`WorkspaceManager` reads and writes files under a single workspace root
(`~/competitions/{competition_name}/` in production). No other module should
touch the workspace filesystem directly — nodes and tools always go through
this class. See `design.md` § WorkspaceManager API for the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook


class WorkspaceManager:
    """Single point of file I/O to the ML workspace.

    All methods accepting a `relative_path` reject absolute paths and any
    `..` traversal component, raising `ValueError` — the workspace root
    cannot be escaped from a relative path argument.
    """

    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace_path: Path = Path(workspace_path).expanduser().resolve()
        self.workspace_path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        p = Path(relative_path)
        if p.is_absolute():
            raise ValueError(
                f"relative_path must be relative, got absolute path: {relative_path!r}"
            )
        if ".." in p.parts:
            raise ValueError(f"relative_path must not contain '..' traversal: {relative_path!r}")
        return self.workspace_path / p

    def read_json(self, relative_path: str) -> dict:
        path = self._resolve(relative_path)
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, relative_path: str, data: dict) -> str:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return str(path)

    def read_text(self, relative_path: str) -> str:
        path = self._resolve(relative_path)
        return path.read_text(encoding="utf-8")

    def write_text(self, relative_path: str, content: str) -> str:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def write_notebook(self, relative_path: str, cells: list) -> str:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        nb = new_notebook()
        for cell in cells:
            cell_type = cell.get("cell_type")
            source = cell.get("source", "")
            if cell_type == "code":
                nb["cells"].append(new_code_cell(source=source))
            elif cell_type == "markdown":
                nb["cells"].append(new_markdown_cell(source=source))
            else:
                raise ValueError(f"Unsupported cell_type: {cell_type!r}")
        nbformat.validate(nb)
        nbformat.write(nb, str(path))
        return str(path)

    def experiment_dir(self, exp_id: str) -> Path:
        return self._resolve(f"experiments/{exp_id}")

    def ensure_dir(self, relative_path: str) -> Path:
        path = self._resolve(relative_path)
        path.mkdir(parents=True, exist_ok=True)
        return path
