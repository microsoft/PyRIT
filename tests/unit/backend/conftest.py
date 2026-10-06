# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend compatibility fixtures independent of a packaged workspace stamp."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit import _compatibility
from pyrit.backend.main import app
from pyrit.backend.services.attack_service import get_attack_service
from pyrit.backend.services.instance_persistence_service import get_instance_persistence_service
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.message_send_service import get_message_send_service
from pyrit.registry.instance_recipe_storage import InstanceRecipeStorage


@pytest.fixture(autouse=True)
def isolated_manual_message_services() -> Iterator[None]:
    """Keep cached service owners bound to this test's memory and event loop."""
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler):
        factory.cache_clear()
    yield
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler):
        factory.cache_clear()


@pytest.fixture(autouse=True)
def isolated_instance_recipes(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Save API-created instances in a per-test directory instead of the user's PyRIT directory."""
    directory = tmp_path_factory.mktemp("instance_recipes")
    get_instance_persistence_service.cache_clear()
    with patch.object(InstanceRecipeStorage, "_get_default_storage_dir", return_value=directory):
        yield directory
    get_instance_persistence_service.cache_clear()


@pytest.fixture(autouse=True)
def compatibility_id() -> Iterator[str]:
    """Supply startup provenance and initialize clients that deliberately skip lifespan."""
    identity = "0.14.0+g" + "a" * 40
    with (
        patch.object(_compatibility, "get_compatibility_id", return_value=identity),
        patch.object(app.state, "compatibility_id", identity, create=True),
    ):
        yield identity


@pytest.fixture
def compatibility_headers(compatibility_id: str) -> dict[str, str]:
    """Provide the marker required by business API requests."""
    return {_compatibility.COMPATIBILITY_HEADER: compatibility_id}
