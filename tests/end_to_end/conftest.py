# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared fixtures for end-to-end tests.

Since the CLI server refactor (#1545), ``pyrit_scan`` is a thin client that
talks to a separate ``pyrit_backend`` process; it exits with an error if no
server answers on ``http://localhost:8000``. The session-scoped fixture
below launches a backend once per test session so the scenario tests can
keep invoking ``pyrit_scan_main`` directly without any CLI changes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pyrit.cli._server_launcher import ServerLauncher

_E2E_CONFIG_FILE = Path(__file__).parent / "test_config.yaml"


@pytest.fixture(scope="session", autouse=True)
def _pyrit_backend_server():
    """Launch ``pyrit_backend`` for the duration of the e2e test session.

    Uses the same ``test_config.yaml`` the scenario tests pass to the CLI
    client so server-side and client-side memory/config stay in sync.

    Tear down via ``launcher.stop()`` so a lingering subprocess does not
    occupy port 8000 between local pytest runs. The launcher is a no-op if
    a backend is already healthy on the port (e.g. a developer has one
    running), so this fixture also remains friendly to local iteration.
    """
    launcher = ServerLauncher()
    asyncio.run(launcher.start_async(config_file=_E2E_CONFIG_FILE))
    yield
    launcher.stop()
