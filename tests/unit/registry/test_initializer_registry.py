# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

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


# ============================================================================
# register_from_content Tests
# ============================================================================

_VALID_SCRIPT = """
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

class ScriptTestInitializer(PyRITInitializer):
    \"\"\"A test initializer from script.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""


def test_register_from_content_discovers_class():
    """Test registering an initializer from uploaded content."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())
        name = registry.register_from_content(name="my_custom", script_content=_VALID_SCRIPT)

        assert name == "my_custom"
        assert "my_custom" in registry


def test_register_from_content_no_classes_raises_value_error():
    """Test that ValueError is raised when content has no initializer classes."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())

        with pytest.raises(ValueError, match="does not contain"):
            registry.register_from_content(name="empty", script_content="x = 1\n")


def test_register_from_content_bad_syntax_raises_value_error():
    """Test that a script with syntax errors raises ValueError."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())

        with pytest.raises(ValueError, match="Failed to load"):
            registry.register_from_content(name="bad", script_content="def bad syntax(:\n")


def test_register_from_content_ignores_imported_classes():
    """Test that imported base classes are not registered."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    script = """
from pyrit.setup.initializers.simple import SimpleInitializer
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

class LocalOnlyInitializer(PyRITInitializer):
    \"\"\"Local only.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""

    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())
        name = registry.register_from_content(name="local_only", script_content=script)

        assert name == "local_only"
        cls = registry.get_class("local_only")
        assert cls.__name__ == "LocalOnlyInitializer"


def test_unregister_and_cleanup_removes_entry_and_file():
    """Test that unregister_and_cleanup removes both registry entry and script file."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    tmp_dir = Path(tempfile.mkdtemp())
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir", return_value=tmp_dir):
        registry.register_from_content(name="cleanup_test", script_content=_VALID_SCRIPT)
        assert "cleanup_test" in registry
        assert (tmp_dir / "cleanup_test.py").exists()

        registry.unregister_and_cleanup("cleanup_test")
        assert "cleanup_test" not in registry
        assert not (tmp_dir / "cleanup_test.py").exists()
