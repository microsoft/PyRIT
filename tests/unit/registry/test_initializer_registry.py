# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from pyrit.registry.class_registries.initializer_registry import (
    PYRIT_PATH,
    InitializerRegistry,
)


def test_initializer_registry_default_discovery_path():
    """Test that InitializerRegistry sets the default discovery path when None is passed."""
    registry = InitializerRegistry(lazy_discovery=True)
    expected = Path(PYRIT_PATH) / "setup" / "initializers"
    assert registry._discovery_path == expected


def test_initializer_registry_custom_discovery_path():
    """Test that InitializerRegistry uses a custom discovery path when provided."""
    custom_path = Path(PYRIT_PATH) / "setup" / "initializers" / "scenarios"
    registry = InitializerRegistry(discovery_path=custom_path, lazy_discovery=True)
    assert registry._discovery_path == custom_path
