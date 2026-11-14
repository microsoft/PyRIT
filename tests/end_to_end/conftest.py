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
import json
from pathlib import Path

import pytest

from pyrit.cli._server_launcher import ServerLauncher

_E2E_CONFIG_FILE = Path(__file__).parent / "test_config.yaml"


@pytest.fixture(scope="session", autouse=True)
def _pyrit_backend_server(tmp_path_factory: pytest.TempPathFactory):
    """Launch ``pyrit_backend`` for the duration of the e2e test session.

    Uses the same ``test_config.yaml`` the scenario tests pass to the CLI
    client so server-side and client-side memory/config stay in sync. The
    backend's copy also points saved targets, converters, scorers, and
    techniques at a fresh folder, so a backend this fixture starts never reads
    or writes the ones saved on the machine running it.

    Tear down via ``launcher.stop()`` so a lingering subprocess does not
    occupy port 8000 between local pytest runs. The launcher is a no-op if
    a backend is already healthy on the port (e.g. a developer has one
    running), so this fixture also remains friendly to local iteration.
    """
    folder = tmp_path_factory.mktemp("pyrit_backend")
    config_file = folder / _E2E_CONFIG_FILE.name
    recipes_source = json.dumps(str(folder / "registry_recipes"))
    config_file.write_text(f"{_E2E_CONFIG_FILE.read_text()}\nregistry_recipes_source: {recipes_source}\n")
    launcher = ServerLauncher()
    asyncio.run(launcher.start_async(config_file=config_file))
    yield
    launcher.stop()
