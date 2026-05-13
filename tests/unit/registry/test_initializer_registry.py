# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path

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
# Unregister Tests
# ============================================================================


def test_unregister_removes_entry():
    """Test that unregister removes an entry from the registry."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    class DummyInitializer(PyRITInitializer):
        """Dummy."""

        async def initialize_async(self) -> None:
            pass

    registry._class_entries["dummy"] = ClassEntry(registered_class=DummyInitializer)
    assert "dummy" in registry

    registry.unregister("dummy")
    assert "dummy" not in registry


def test_unregister_raises_key_error_for_missing():
    """Test that unregister raises KeyError for non-existent entry."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with pytest.raises(KeyError, match="nonexistent"):
        registry.unregister("nonexistent")


def test_unregister_invalidates_metadata_cache():
    """Test that unregister invalidates the metadata cache."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    class CachedInitializer(PyRITInitializer):
        """Cached."""

        async def initialize_async(self) -> None:
            pass

    registry._class_entries["cached"] = ClassEntry(registered_class=CachedInitializer)
    registry.list_metadata()
    assert registry._metadata_cache is not None

    registry.unregister("cached")
    assert registry._metadata_cache is None


# ============================================================================
# register_from_script Tests
# ============================================================================


def test_register_from_script_discovers_class():
    """Test registering an initializer from a script file."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            """
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

class ScriptTestInitializer(PyRITInitializer):
    \"\"\"A test initializer from script.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""
        )
        script_path = Path(f.name)

    try:
        names = registry.register_from_script(script_path=script_path)
        assert names == ["script_test"]
        assert "script_test" in registry
    finally:
        script_path.unlink()


def test_register_from_script_with_custom_name():
    """Test registering with a custom name."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            """
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

class AnotherInitializer(PyRITInitializer):
    \"\"\"Another init.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""
        )
        script_path = Path(f.name)

    try:
        names = registry.register_from_script(script_path=script_path, name="my_custom_name")
        assert names == ["my_custom_name"]
        assert "my_custom_name" in registry
    finally:
        script_path.unlink()


def test_register_from_script_file_not_found():
    """Test that FileNotFoundError is raised for missing script."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with pytest.raises(FileNotFoundError):
        registry.register_from_script(script_path=Path("/nonexistent/init.py"))


def test_register_from_script_no_classes():
    """Test that ValueError is raised when script has no initializer classes."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("x = 1\n")
        script_path = Path(f.name)

    try:
        with pytest.raises(ValueError, match="does not contain"):
            registry.register_from_script(script_path=script_path)
    finally:
        script_path.unlink()


def test_register_from_script_ignores_imported_classes():
    """Test that imported base classes are not registered."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            """
from pyrit.setup.initializers.simple import SimpleInitializer
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

class LocalOnlyInitializer(PyRITInitializer):
    \"\"\"Local only.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""
        )
        script_path = Path(f.name)

    try:
        names = registry.register_from_script(script_path=script_path)
        assert "local_only" in names
        assert "simple" not in names
    finally:
        script_path.unlink()


def test_register_from_script_bad_script_raises_value_error():
    """Test that a script with syntax errors raises ValueError."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def bad syntax(:\n")
        script_path = Path(f.name)

    try:
        with pytest.raises(ValueError, match="Failed to load"):
            registry.register_from_script(script_path=script_path)
    finally:
        script_path.unlink()


def test_register_from_script_non_py_raises_value_error():
    """Test that non-.py files raise ValueError."""
    registry = InitializerRegistry(lazy_discovery=True)
    registry._discovered = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("not python\n")
        script_path = Path(f.name)

    try:
        with pytest.raises(ValueError, match="must be a Python file"):
            registry.register_from_script(script_path=script_path)
    finally:
        script_path.unlink()
