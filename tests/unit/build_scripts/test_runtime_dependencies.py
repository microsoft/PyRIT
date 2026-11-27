# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_async_azure_transport_is_a_required_runtime_dependency() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [Requirement(value) for value in project["project"]["dependencies"]]

    assert any(dependency.name == "aiohttp" and dependency.marker is None for dependency in dependencies), (
        "Async Azure credentials and Key Vault clients require aiohttp in a base PyRIT installation; "
        "a uv constraint or a transitive dependency does not guarantee it is installed."
    )
