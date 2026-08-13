"""Test fixtures package.

Makes `tests.fixtures` an explicit regular package (rather than relying on
implicit namespace packages) so `tests.fixtures.graph_mocks` imports
identically regardless of how pytest is invoked.
"""
