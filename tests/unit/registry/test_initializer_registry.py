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


@pytest.fixture
def lazy_registry() -> InitializerRegistry:
    """Create an InitializerRegistry with lazy discovery already marked as complete."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True
    return registry


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


def test_register_from_content_discovers_class(lazy_registry):
    """Test registering an initializer from uploaded content."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())
        name = lazy_registry.register_from_content(name="my_custom", script_content=_VALID_SCRIPT)

        assert name == "my_custom"
        assert "my_custom" in lazy_registry


@pytest.mark.parametrize("bad_name", ["../traversal", "UPPER", "has space", "1digit", ""])
def test_register_from_content_rejects_invalid_name(lazy_registry, bad_name):
    """Test that register_from_content rejects names that fail registry name validation."""
    with pytest.raises(ValueError, match="Invalid registry name"):
        lazy_registry.register_from_content(name=bad_name, script_content=_VALID_SCRIPT)


def test_register_from_content_no_classes_raises_value_error(lazy_registry):
    """Test that ValueError is raised when content has no initializer classes."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())

        with pytest.raises(ValueError, match="does not contain"):
            lazy_registry.register_from_content(name="empty", script_content="x = 1\n")


def test_register_from_content_bad_syntax_raises_value_error(lazy_registry):
    """Test that a script with syntax errors raises ValueError."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        tmp_dir = Path(tempfile.mkdtemp())
        mock_dir.return_value = tmp_dir

        with pytest.raises(ValueError, match="Failed to load"):
            lazy_registry.register_from_content(name="bad", script_content="def bad syntax(:\n")


def test_register_from_content_bad_syntax_cleans_up_file(lazy_registry):
    """Test that a failed import cleans up the script file."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        tmp_dir = Path(tempfile.mkdtemp())
        mock_dir.return_value = tmp_dir

        with pytest.raises(ValueError):
            lazy_registry.register_from_content(name="orphan", script_content="def bad syntax(:\n")

        assert not (tmp_dir / "orphan.py").exists()


def test_register_from_content_no_class_cleans_up_file(lazy_registry):
    """Test that missing initializer class cleans up the script file."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        tmp_dir = Path(tempfile.mkdtemp())
        mock_dir.return_value = tmp_dir

        with pytest.raises(ValueError, match="does not contain"):
            lazy_registry.register_from_content(name="no_class", script_content="x = 1\n")

        assert not (tmp_dir / "no_class.py").exists()


def test_register_from_content_rejects_duplicate_name(lazy_registry):
    """Test that registering over an existing name raises ValueError."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())
        lazy_registry.register_from_content(name="dup", script_content=_VALID_SCRIPT)

        with pytest.raises(ValueError, match="already registered"):
            lazy_registry.register_from_content(name="dup", script_content=_VALID_SCRIPT)


def test_register_from_content_ignores_imported_classes(lazy_registry):
    """Test that imported base classes are not registered."""
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
        name = lazy_registry.register_from_content(name="local_only", script_content=script)

        assert name == "local_only"
        cls = lazy_registry.get_class("local_only")
        assert cls.__name__ == "LocalOnlyInitializer"


def test_unregister_and_cleanup_removes_entry_and_file(lazy_registry):
    """Test that unregister_and_cleanup removes both registry entry and script file."""
    tmp_dir = Path(tempfile.mkdtemp())
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir", return_value=tmp_dir):
        lazy_registry.register_from_content(name="cleanup_test", script_content=_VALID_SCRIPT)
        assert "cleanup_test" in lazy_registry
        assert (tmp_dir / "cleanup_test.py").exists()

        lazy_registry.unregister_and_cleanup("cleanup_test")
        assert "cleanup_test" not in lazy_registry
        assert not (tmp_dir / "cleanup_test.py").exists()


def test_unregister_and_cleanup_rejects_builtin(lazy_registry):
    """Test that unregister_and_cleanup raises ValueError for built-in initializers."""

    class BuiltinInit(PyRITInitializer):
        async def initialize_async(self) -> None:
            pass

    entry = ClassEntry(registered_class=BuiltinInit)
    lazy_registry._class_entries["builtin_test"] = entry
    lazy_registry._builtin_names.add("builtin_test")

    with pytest.raises(ValueError, match="Cannot remove built-in"):
        lazy_registry.unregister_and_cleanup("builtin_test")

    assert "builtin_test" in lazy_registry


def test_is_builtin_returns_true_for_discovered_initializers(lazy_registry):
    """Test that is_builtin correctly identifies built-in entries."""

    class FakeInit(PyRITInitializer):
        async def initialize_async(self) -> None:
            pass

    entry = ClassEntry(registered_class=FakeInit)
    lazy_registry._class_entries["fake"] = entry
    lazy_registry._builtin_names.add("fake")

    assert lazy_registry.is_builtin("fake") is True


def test_is_builtin_returns_false_for_custom_initializers(lazy_registry):
    """Test that is_builtin returns False for custom-registered entries."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())
        lazy_registry.register_from_content(name="custom", script_content=_VALID_SCRIPT)

    assert lazy_registry.is_builtin("custom") is False
