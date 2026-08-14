"""Test fixtures package.

Makes `tests.fixtures` an explicit regular package rather than an implicit
namespace package, so `tests.fixtures.graph_mocks` is an unambiguous module
path.

This does not by itself make the import work everywhere: `tests/` has no
`__init__.py`, so `tests` is still an implicit namespace package, and the
import resolves because the root `conftest.py` puts the rootdir on `sys.path`
under pytest's default `prepend` import mode. It could still need revisiting
under `--import-mode=importlib`.
"""
