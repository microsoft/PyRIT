# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.models.parameter import Parameter
from pyrit.registry.components.initializer_registry import PYRIT_PATH, InitializerRegistry
from pyrit.setup.pyrit_initializer import PyRITInitializer


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
    metadata = registry._build_metadata("fake", FakeInitializer)

    assert metadata.class_description == "A fake initializer for testing."
    assert metadata.class_name == "FakeInitializer"
    assert metadata.registry_name == "fake"


# ============================================================================
# register_from_content Tests
# ============================================================================

_VALID_SCRIPT = """
from pyrit.setup.pyrit_initializer import PyRITInitializer

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
from pyrit.setup.initializers.targets import TargetInitializer
from pyrit.setup.pyrit_initializer import PyRITInitializer

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

    lazy_registry._classes["builtin_test"] = BuiltinInit
    lazy_registry._builtin_names.add("builtin_test")

    with pytest.raises(ValueError, match="Cannot remove built-in"):
        lazy_registry.unregister_and_cleanup("builtin_test")

    assert "builtin_test" in lazy_registry


def test_is_builtin_returns_true_for_discovered_initializers(lazy_registry):
    """Test that is_builtin correctly identifies built-in entries."""

    class FakeInit(PyRITInitializer):
        async def initialize_async(self) -> None:
            pass

    lazy_registry._classes["fake"] = FakeInit
    lazy_registry._builtin_names.add("fake")

    assert lazy_registry.is_builtin("fake") is True


def test_is_builtin_returns_false_for_custom_initializers(lazy_registry):
    """Test that is_builtin returns False for custom-registered entries."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir") as mock_dir:
        mock_dir.return_value = Path(tempfile.mkdtemp())
        lazy_registry.register_from_content(name="custom", script_content=_VALID_SCRIPT)

    assert lazy_registry.is_builtin("custom") is False


# ============================================================================
# create_from_script_paths Tests
# ============================================================================


def _write_initializer_script(directory: Path, filename: str, *class_names: str) -> Path:
    """Write a script defining one or more PyRITInitializer subclasses."""
    body = "from pyrit.setup.pyrit_initializer import PyRITInitializer\n\n"
    for class_name in class_names:
        body += (
            f"class {class_name}(PyRITInitializer):\n    async def initialize_async(self) -> None:\n        pass\n\n"
        )
    script_path = directory / filename
    script_path.write_text(body)
    return script_path


def test_create_from_script_paths_loads_multiple_classes(lazy_registry):
    """Test that all initializer subclasses defined in a file are instantiated."""
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = _write_initializer_script(Path(temp_dir), "multi.py", "FirstInit", "SecondInit")

        instances = lazy_registry.create_from_script_paths(script_paths=[script_path])

        assert {type(i).__name__ for i in instances} == {"FirstInit", "SecondInit"}
        # Loading does not add the classes to the catalog.
        assert lazy_registry.get_class_names() == []


def test_create_from_script_paths_rejects_non_python_file(lazy_registry):
    """Test that a non-.py path raises ValueError."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bad_path = Path(temp_dir) / "not_python.txt"
        bad_path.write_text("hello")

        with pytest.raises(ValueError, match="must be a Python file"):
            lazy_registry.create_from_script_paths(script_paths=[bad_path])


def test_create_from_script_paths_no_subclass_raises_value_error(lazy_registry):
    """Test that a file defining no initializer subclass raises ValueError."""
    with tempfile.TemporaryDirectory() as temp_dir:
        empty_path = Path(temp_dir) / "empty.py"
        empty_path.write_text("x = 1\n")

        with pytest.raises(ValueError, match="must contain at least one"):
            lazy_registry.create_from_script_paths(script_paths=[empty_path])


def test_create_from_script_paths_missing_file_raises(lazy_registry):
    """Test that a missing script path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        lazy_registry.create_from_script_paths(script_paths=["definitely_missing_script.py"])


# ============================================================================
# create_and_configure Tests
# ============================================================================


class _ParamInitializer(PyRITInitializer):
    """An initializer that accepts a single declared parameter."""

    @property
    def supported_parameters(self) -> list[Parameter]:
        return [Parameter(name="mode", description="Operation mode", default="fast")]

    async def initialize_async(self) -> None:
        pass


def test_create_and_configure_builds_and_sets_params(lazy_registry):
    """Test that create_and_configure returns a configured instance with params set."""
    lazy_registry.register_class(_ParamInitializer, name="param_init")

    instance = lazy_registry.create_and_configure("param_init", initializer_params={"mode": "slow"})

    assert isinstance(instance, _ParamInitializer)
    assert instance.params == {"mode": ["slow"]}


def test_create_and_configure_without_params_leaves_instance_unconfigured(lazy_registry):
    """Test that create_and_configure returns an unconfigured instance when no params are given."""
    lazy_registry.register_class(_ParamInitializer, name="param_init")

    instance = lazy_registry.create_and_configure("param_init")

    assert isinstance(instance, _ParamInitializer)
    assert instance.params == {}


def test_create_and_configure_unknown_param_raises_value_error(lazy_registry):
    """Test that an unknown parameter raises ValueError during configuration."""
    lazy_registry.register_class(_ParamInitializer, name="param_init")

    with pytest.raises(ValueError, match="unknown parameter"):
        lazy_registry.create_and_configure("param_init", initializer_params={"bogus": "x"})


def test_create_and_configure_unknown_name_raises_key_error(lazy_registry):
    """Test that an unregistered name raises KeyError."""
    with pytest.raises(KeyError):
        lazy_registry.create_and_configure("does_not_exist")


# ============================================================================
# Discovery / registration edge-case Tests
# ============================================================================

_SOLO_SCRIPT = (
    "from pyrit.setup.pyrit_initializer import PyRITInitializer\n\n"
    "class SoloInitializer(PyRITInitializer):\n"
    "    async def initialize_async(self) -> None:\n"
    "        pass\n"
)


def test_discover_directory_registers_and_lists_metadata():
    """Test that a directory discovery path scans, registers, and builds metadata for initializers."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "solo.py").write_text(_SOLO_SCRIPT)

        registry = InitializerRegistry(discovery_path=Path(temp_dir), lazy_discovery=False)

        assert "solo" in registry
        metadata = registry.get_all_registered_class_metadata()
        assert any(m.registry_name == "solo" for m in metadata)


def test_discover_single_file_registers_builtin():
    """Test that a discovery path pointing at a single file registers it as built-in."""
    with tempfile.TemporaryDirectory() as temp_dir:
        script = Path(temp_dir) / "solo.py"
        script.write_text(_SOLO_SCRIPT)

        registry = InitializerRegistry(discovery_path=script, lazy_discovery=False)

        assert "solo" in registry
        assert registry.is_builtin("solo") is True
        assert registry.is_builtin("not_registered") is False


def test_discover_missing_path_registers_nothing():
    """Test that a non-existent discovery path logs a warning and registers nothing."""
    missing = Path(tempfile.gettempdir()) / "pyrit_missing_initializers_dir_xyz"
    registry = InitializerRegistry(discovery_path=missing, lazy_discovery=False)

    assert registry.get_class_names() == []


def test_discover_single_file_load_failure_registers_nothing():
    """Test that a file that fails to import is skipped without raising."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bad = Path(temp_dir) / "bad.py"
        bad.write_text("def bad syntax(:\n")

        registry = InitializerRegistry(discovery_path=bad, lazy_discovery=False)

        assert registry.get_class_names() == []


def test_register_initializer_collision_keeps_first(lazy_registry):
    """Test that a registry-name collision keeps the first registration."""

    class DupInitializer(PyRITInitializer):
        async def initialize_async(self) -> None:
            pass

    lazy_registry._register_initializer(initializer_class=DupInitializer, builtin=True)
    lazy_registry._register_initializer(initializer_class=DupInitializer)

    assert lazy_registry.get_class("dup") is DupInitializer


def test_register_initializer_swallows_registration_errors(lazy_registry):
    """Test that a failure inside register_class is logged and swallowed."""

    class BadInitializer(PyRITInitializer):
        async def initialize_async(self) -> None:
            pass

    with patch.object(lazy_registry, "register_class", side_effect=RuntimeError("boom")):
        lazy_registry._register_initializer(initializer_class=BadInitializer)

    assert "bad" not in lazy_registry


def test_build_metadata_instantiation_failure_returns_fallback(lazy_registry):
    """Test that _build_metadata falls back when the initializer cannot be instantiated."""

    class ExplodingInitializer(PyRITInitializer):
        """Exploding."""

        def __init__(self) -> None:
            raise RuntimeError("cannot construct")

        async def initialize_async(self) -> None:
            pass

    metadata = lazy_registry._build_metadata("exploding", ExplodingInitializer)

    assert metadata.class_description == "Error loading initializer metadata"
    assert metadata.required_env_vars == ()


def test_load_module_from_path_no_spec_raises():
    """Test that _load_module_from_path raises when an import spec cannot be created."""
    with patch(
        "pyrit.registry.components.initializer_registry.importlib.util.spec_from_file_location",
        return_value=None,
    ):
        with pytest.raises(ValueError, match="Could not load initializer script"):
            InitializerRegistry._load_module_from_path(file_path=Path("nope.py"), module_name="nope")


def test_create_from_script_paths_instantiation_failure_raises(lazy_registry):
    """Test that a script whose only initializer fails to instantiate raises ValueError."""
    script = (
        "from pyrit.setup.pyrit_initializer import PyRITInitializer\n\n"
        "class BoomInitializer(PyRITInitializer):\n"
        "    def __init__(self):\n"
        "        raise RuntimeError('boom')\n"
        "    async def initialize_async(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "boom.py"
        path.write_text(script)

        with pytest.raises(ValueError, match="must contain at least one"):
            lazy_registry.create_from_script_paths(script_paths=[path])


def test_register_from_content_write_failure_raises(lazy_registry):
    """Test that an OSError while writing the script surfaces as ValueError."""
    with patch.object(InitializerRegistry, "_get_custom_scripts_dir", return_value=Path(tempfile.mkdtemp())):
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(ValueError, match="Failed to write initializer script"):
                lazy_registry.register_from_content(name="write_fail", script_content=_VALID_SCRIPT)


def test_unregister_and_cleanup_unknown_name_raises(lazy_registry):
    """Test that unregistering an unknown, non-built-in name raises KeyError."""
    with pytest.raises(KeyError, match="not found in registry"):
        lazy_registry.unregister_and_cleanup("nonexistent")


def test_get_custom_scripts_dir_creates_directory():
    """Test that _get_custom_scripts_dir returns and creates the managed directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch("pyrit.common.path.CONFIGURATION_DIRECTORY_PATH", Path(temp_dir)):
            result = InitializerRegistry._get_custom_scripts_dir()

        assert result == Path(temp_dir) / "custom_initializers"
        assert result.exists()
