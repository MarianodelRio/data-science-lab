"""Thin wrapper around the `kaggle` package for downloading competition data,
submitting predictions, and reading back the latest score.

**Why `kaggle` is never imported at module scope:** the `kaggle` package
authenticates eagerly on import. `kaggle/__init__.py` does
`from kaggle.api.kaggle_api_extended import KaggleApi; api = KaggleApi();
api.authenticate()` — and because Python always initializes a parent package
before a submodule, even `from kaggle.api.kaggle_api_extended import
KaggleApi` runs that top-level code. `authenticate()` reads
`KAGGLE_USERNAME`/`KAGGLE_KEY` from the environment, falls back to
`~/.kaggle/kaggle.json`, and raises `OSError` if neither is present. If this
module imported `kaggle` at module scope, merely importing
`src.tools.kaggle_client` would crash in any credential-less environment
(e.g. CI), and it would crash with `kaggle`'s own `OSError` instead of this
module's `RuntimeError` naming the missing variable. So `_default_api()`
checks the env vars itself first, via `_require_env`, and only imports
`kaggle` afterwards, once credentials are known to be present.
"""

from __future__ import annotations

import os
import re
import zipfile
from typing import Any, Protocol

# Kaggle competition slugs are alphanumeric with internal `-`/`_` (e.g.
# "titanic", "house-prices-advanced-regression-techniques"). Validating
# against this shape before any path construction rejects `competition`
# values like "/etc/passwd" or "../evil" — `os.path.join(dest_dir, ...)`
# silently discards `dest_dir` entirely when the second argument is
# absolute, which would otherwise hand an attacker-chosen absolute path to
# `os.remove()` in `download()`.
_COMPETITION_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _validate_competition(competition: str) -> None:
    if not _COMPETITION_SLUG_RE.match(competition):
        raise ValueError(f"Invalid competition slug: {competition!r}")


class KaggleApiProtocol(Protocol):
    def authenticate(self) -> None: ...

    def competition_download_files(
        self, competition: str, path: str, force: bool = False, quiet: bool = True
    ) -> None: ...

    def competition_submit(
        self, file_name: str, message: str, competition: str, quiet: bool = False
    ) -> None: ...

    def competition_submissions(self, competition: str) -> list[Any]: ...


def _require_env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"Missing required environment variable '{name}'") from None


def _default_api() -> KaggleApiProtocol:
    # Check credentials BEFORE importing kaggle at all — see module docstring.
    _require_env("KAGGLE_USERNAME")
    _require_env("KAGGLE_KEY")
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    return api


def download(competition: str, dest_dir: str, api: KaggleApiProtocol | None = None) -> list[str]:
    """Download and unzip `competition`'s data into `dest_dir`, returning the
    extracted file paths.

    `kaggle`'s `competition_download_files` (this installed version) has no
    built-in unzip — it writes a single `{dest_dir}/{competition}.zip`, which
    is extracted here and then removed.
    """
    _validate_competition(competition)
    resolved_api = api or _default_api()
    os.makedirs(dest_dir, exist_ok=True)
    resolved_api.competition_download_files(competition, path=dest_dir, force=True, quiet=True)

    archive_path = os.path.join(dest_dir, f"{competition}.zip")
    with zipfile.ZipFile(archive_path) as zf:
        member_names = [n for n in zf.namelist() if not n.endswith("/")]
        zf.extractall(dest_dir)
    os.remove(archive_path)

    return [os.path.join(dest_dir, name) for name in member_names]


def submit(
    competition: str,
    file_path: str,
    message: str,
    api: KaggleApiProtocol | None = None,
) -> None:
    _validate_competition(competition)
    resolved_api = api or _default_api()
    resolved_api.competition_submit(
        file_name=file_path, message=message, competition=competition, quiet=True
    )


def get_score(competition: str, api: KaggleApiProtocol | None = None) -> dict[str, Any]:
    """Return `{public_score, submitted_at}` for `competition`'s latest submission.

    "Latest" is determined by `max(.date)`, not list order/index — the
    `kaggle` API does not document `competition_submissions` as returning a
    sorted list.
    """
    _validate_competition(competition)
    resolved_api = api or _default_api()
    submissions = resolved_api.competition_submissions(competition)
    if not submissions:
        raise RuntimeError(f"No submissions found for competition '{competition}'")
    latest = max(submissions, key=lambda s: s.date)
    return {
        "public_score": float(latest.public_score),
        "submitted_at": latest.date.isoformat(),
    }
