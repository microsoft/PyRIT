# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Guard localhost-only publication of local Docker services."""

from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    ("service", "port"),
    [("pyrit-gui", 8000), ("pyrit-jupyter", 8888)],
)
def test_compose_publishes_only_on_loopback(*, service: str, port: int) -> None:
    compose_path = Path(__file__).resolve().parents[3] / "docker" / "docker-compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert compose["services"][service]["ports"] == [f"127.0.0.1:{port}:{port}"]
