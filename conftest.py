"""Root pytest configuration.

Treats "no tests collected" as success rather than failure (pytest's default
exit code 5) -- early-scaffold and infra-only PRs legitimately add zero tests.
"""


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 5:  # no tests collected
        session.exitstatus = 0
