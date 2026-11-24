# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Regression test for the async fixture setup error cascade (https://github.com/pytest-dev/pytest/issues/14800).

The test runs a small pytest project in a subprocess, so the injected loop failure and the
fixture state it leaves behind cannot leak into this session.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from unit.async_utils import TEST_WATCHDOG_SECONDS

_REPO_ROOT = Path(__file__).resolve().parents[2]

_INNER_CONFTEST = textwrap.dedent(
    """
    import asyncio

    import pytest

    from test_support.async_fixture_errors import pytest_fixture_setup  # noqa: F401

    _calls = []


    def _fail_first_loop():
        _calls.append(None)
        if len(_calls) == 1:
            raise OSError(10055, "simulated WSAENOBUFS")
        return asyncio.new_event_loop()


    @pytest.fixture(scope="session")
    def _asyncio_loop_factory():
        return _fail_first_loop


    @pytest.fixture
    async def resource():
        return "ready"
    """
)

_INNER_TESTS = textwrap.dedent(
    """
    async def test_first(resource):
        assert resource == "ready"


    async def test_second(resource):
        assert resource == "ready"


    async def test_third(resource):
        assert resource == "ready"


    async def test_fourth(resource):
        assert resource == "ready"
    """
)


def test_async_fixture_loop_error_is_reported_once_and_later_tests_run(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nasyncio_mode = auto\nasyncio_default_fixture_loop_scope = function\n", encoding="utf-8"
    )
    (tmp_path / "conftest.py").write_text(_INNER_CONFTEST, encoding="utf-8")
    (tmp_path / "test_inner.py").write_text(_INNER_TESTS, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS"}}
    env["PYTHONPATH"] = str(_REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-rA",
            "--tb=native",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=TEST_WATCHDOG_SECONDS,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "3 passed, 1 error" in output, output
    assert "ERROR test_inner.py::test_first" in output, output
    assert "simulated WSAENOBUFS" in output, output
    assert "assert not self._finalizers" not in output, output
