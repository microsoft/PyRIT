# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from pyrit.registry.class_registries.base_class_registry import ClassEntry
from pyrit.registry.class_registries.initializer_registry import (
    PYRIT_PATH,
    InitializerRegistry,
)
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer


def test_initializer_registry_default_discovery_path():
    """Test that InitializerRegistry sets the default discovery path when None is passed."""
    registry = InitializerRegistry(lazy_discovery=True)
    expected = Path(PYRIT_PATH) / "setup" / "initializers"
    assert registry._discovery_path == expected


def test_initializer_registry_custom_discovery_path():
    """Test that InitializerRegistry uses a custom discovery path when provided."""
    custom_path = Path(PYRIT_PATH) / "setup" / "initializers" / "components"
    registry = InitializerRegistry(discovery_path=custom_path, lazy_discovery=True)
    assert registry._discovery_path == custom_path


def test_build_metadata_uses_docstring_description():
    """Test that _build_metadata extracts description from class docstring."""

    class FakeInitializer(PyRITInitializer):
        """A fake initializer for testing."""

        async def initialize_async(self) -> None:
            pass

    registry = InitializerRegistry(lazy_discovery=True)
    entry = ClassEntry(registered_class=FakeInitializer)
    metadata = registry._build_metadata("fake", entry)

    assert metadata.class_description == "A fake initializer for testing."
    assert metadata.class_name == "FakeInitializer"
    assert metadata.registry_name == "fake"
