# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys

import pytest

from pyrit.common.apply_defaults import (
    reset_default_values,
    set_default_value,
    set_global_variable,
)
from pyrit.setup.initializers import InitializerParameter, PyRITInitializer


class TestPyRITInitializerBase:
    """Tests for PyRITInitializer base class."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()
        # Clean up any test globals
        if hasattr(sys.modules["__main__"], "test_var"):
            delattr(sys.modules["__main__"], "test_var")

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()
        if hasattr(sys.modules["__main__"], "test_var"):
            delattr(sys.modules["__main__"], "test_var")

    def test_cannot_instantiate_abstract_class(self):
        """Test that PyRITInitializer cannot be instantiated directly."""
        with pytest.raises(TypeError):
            PyRITInitializer()  # type: ignore[abstract]

    def test_concrete_initializer_can_be_created(self):
        """Test that concrete subclass can be instantiated."""

        class ConcreteInitializer(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Concrete"

            @property
            def description(self) -> str:
                return "Concrete initializer"

            async def initialize_async(self) -> None:
                pass

        init = ConcreteInitializer()
        assert init is not None

    def test_name_property_is_abstract(self):
        """Test that name property must be implemented."""

        class MissingName(PyRITInitializer):
            @property
            def description(self) -> str:
                return "Missing name"

            async def initialize_async(self) -> None:
                pass

        with pytest.raises(TypeError):
            MissingName()  # type: ignore[abstract]

    def test_initialize_method_is_abstract(self):
        """Test that initialize method must be implemented."""

        class MissingInitialize(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Missing init"

            @property
            def description(self) -> str:
                return "Missing initialize"

        with pytest.raises(TypeError):
            MissingInitialize()  # type: ignore[abstract]

    def test_default_execution_order_is_one(self):
        """Test that default execution order is 1."""

        class DefaultOrder(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Default"

            @property
            def description(self) -> str:
                return "Default order"

            async def initialize_async(self) -> None:
                pass

        init = DefaultOrder()
        assert init.execution_order == 1

    def test_execution_order_can_be_overridden(self):
        """Test that execution order can be customized."""

        class CustomOrder(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Custom"

            @property
            def description(self) -> str:
                return "Custom order"

            @property
            def execution_order(self) -> int:
                return 5

            async def initialize_async(self) -> None:
                pass

        init = CustomOrder()
        assert init.execution_order == 5

    def test_default_required_env_vars_is_empty(self):
        """Test that default required_env_vars is empty list."""

        class NoEnvVars(PyRITInitializer):
            @property
            def name(self) -> str:
                return "No env"

            @property
            def description(self) -> str:
                return "No env vars"

            async def initialize_async(self) -> None:
                pass

        init = NoEnvVars()
        assert init.required_env_vars == []

    def test_required_env_vars_can_be_overridden(self):
        """Test that required_env_vars can be customized."""

        class WithEnvVars(PyRITInitializer):
            @property
            def name(self) -> str:
                return "With env"

            @property
            def description(self) -> str:
                return "With env vars"

            @property
            def required_env_vars(self):
                return ["API_KEY", "ENDPOINT"]

            async def initialize_async(self) -> None:
                pass

        init = WithEnvVars()
        assert init.required_env_vars == ["API_KEY", "ENDPOINT"]

    def test_default_validate_does_nothing(self):
        """Test that default validate method does nothing."""

        class DefaultValidate(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Default validate"

            @property
            def description(self) -> str:
                return "Default validation"

            async def initialize_async(self) -> None:
                pass

        init = DefaultValidate()
        # Should not raise any errors
        init.validate()

    def test_validate_can_be_overridden(self):
        """Test that validate method can be customized."""

        class CustomValidate(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Custom validate"

            @property
            def description(self) -> str:
                return "Custom validation"

            def validate(self) -> None:
                raise ValueError("Validation failed")

            async def initialize_async(self) -> None:
                pass

        init = CustomValidate()
        with pytest.raises(ValueError, match="Validation failed"):
            init.validate()


class TestInitializeWithTracking:
    """Tests for initialize_with_tracking_async method."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()
        if hasattr(sys.modules["__main__"], "tracked_var"):
            delattr(sys.modules["__main__"], "tracked_var")

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()
        if hasattr(sys.modules["__main__"], "tracked_var"):
            delattr(sys.modules["__main__"], "tracked_var")

    async def test_initialize_with_tracking_calls_initialize(self):
        """Test that initialize_with_tracking_async calls initialize method."""
        executed = False

        class TrackableInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Trackable"

            @property
            def description(self) -> str:
                return "Trackable init"

            async def initialize_async(self) -> None:
                nonlocal executed
                executed = True

        init = TrackableInit()
        await init.initialize_with_tracking_async()
        assert executed

    async def test_initialize_with_tracking_captures_default_values(self):
        """Test that tracking captures default values."""

        class DummyClass:
            def __init__(self, *, value: str = "default") -> None:
                self.value = value

        class TrackingInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Tracking"

            @property
            def description(self) -> str:
                return "Tracking defaults"

            async def initialize_async(self) -> None:
                set_default_value(class_type=DummyClass, parameter_name="value", value="tracked")

        init = TrackingInit()
        await init.initialize_with_tracking_async()

        # Verify the default was actually set
        from pyrit.common.apply_defaults import get_global_default_values

        registry = get_global_default_values()
        assert len(registry._default_values) > 0

    async def test_initialize_with_tracking_captures_global_variables(self):
        """Test that tracking captures global variables."""

        class GlobalVarInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Global var"

            @property
            def description(self) -> str:
                return "Sets global var"

            async def initialize_async(self) -> None:
                set_global_variable(name="tracked_var", value="test_value")

        init = GlobalVarInit()
        await init.initialize_with_tracking_async()

        # Verify the global variable was set
        assert hasattr(sys.modules["__main__"], "tracked_var")
        assert sys.modules["__main__"].tracked_var == "test_value"


class TestGetInfo:
    """Tests for get_info class method."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()

    async def test_get_info_returns_dict(self):
        """Test that get_info returns a dictionary."""

        class InfoInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Info test"

            @property
            def description(self) -> str:
                return "For testing get_info"

            async def initialize_async(self) -> None:
                pass

        info = await InfoInit.get_info_async()
        assert isinstance(info, dict)

    async def test_get_info_contains_basic_fields(self):
        """Test that get_info contains name, description, and class."""

        class BasicInfoInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Basic Info"

            @property
            def description(self) -> str:
                return "Basic description"

            async def initialize_async(self) -> None:
                pass

        info = await BasicInfoInit.get_info_async()
        assert "name" in info
        assert "description" in info
        assert "class" in info
        assert "execution_order" in info

        assert info["name"] == "Basic Info"
        assert info["description"] == "Basic description"
        assert info["class"] == "BasicInfoInit"
        assert info["execution_order"] == 1

    async def test_get_info_includes_required_env_vars_when_present(self):
        """Test that get_info includes required_env_vars when defined."""

        class EnvVarsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Env vars"

            @property
            def description(self) -> str:
                return "With env vars"

            @property
            def required_env_vars(self):
                return ["API_KEY"]

            async def initialize_async(self) -> None:
                pass

        info = await EnvVarsInit.get_info_async()
        assert "required_env_vars" in info
        assert info["required_env_vars"] == ["API_KEY"]

    async def test_get_info_omits_required_env_vars_when_empty(self):
        """Test that get_info omits required_env_vars when empty."""

        class NoEnvVarsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "No env"

            @property
            def description(self) -> str:
                return "No env vars"

            async def initialize_async(self) -> None:
                pass

        info = await NoEnvVarsInit.get_info_async()
        # Should not have required_env_vars key if empty
        assert "required_env_vars" not in info

    async def test_get_info_is_class_method(self):
        """Test that get_info can be called as a class method."""

        class ClassMethodInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Class method"

            @property
            def description(self) -> str:
                return "For class method test"

            async def initialize_async(self) -> None:
                pass

        # Should work without creating an instance
        info = await ClassMethodInit.get_info_async()
        assert info["name"] == "Class method"

    async def test_get_info_includes_default_values_info(self):
        """Test that get_info includes information about default values."""

        class DefaultsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Defaults"

            @property
            def description(self) -> str:
                return "Sets defaults"

            async def initialize_async(self) -> None:
                pass

        info = await DefaultsInit.get_info_async()
        # Should have default_values and global_variables keys
        assert "default_values" in info
        assert "global_variables" in info


@pytest.mark.usefixtures("patch_central_database")
class TestGetInfoTracking:
    """Tests for get_info tracking of default values and global variables."""

    def setup_method(self) -> None:
        """Clear default values and globals before each test."""
        reset_default_values()
        # Clean up test globals
        test_vars = ["test_global_var", "test_converter_target"]
        for var in test_vars:
            if hasattr(sys.modules["__main__"], var):
                delattr(sys.modules["__main__"], var)

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()
        test_vars = ["test_global_var", "test_converter_target"]
        for var in test_vars:
            if hasattr(sys.modules["__main__"], var):
                delattr(sys.modules["__main__"], var)

    async def test_get_info_correctly_tracks_defaults_and_globals(self):
        """Test that get_info correctly tracks both default values and global variables."""

        class DummyTarget:
            def __init__(self, *, endpoint: str = "default") -> None:
                self.endpoint = endpoint

        class DummyConverter:
            def __init__(self, *, target: str = "default") -> None:
                self.target = target

        class CompleteInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Complete Tracker"

            @property
            def description(self) -> str:
                return "Sets both defaults and globals"

            async def initialize_async(self) -> None:
                # Set default values
                set_default_value(class_type=DummyTarget, parameter_name="endpoint", value="custom_endpoint")
                set_default_value(class_type=DummyConverter, parameter_name="target", value="custom_target")

                # Set global variables
                set_global_variable(name="test_global_var", value="test_value")
                set_global_variable(name="test_converter_target", value="converter")

        info = await CompleteInit.get_info_async()

        # Verify default_values tracking - should use "ClassName.parameter_name" format
        assert isinstance(info["default_values"], list)
        assert "DummyTarget.endpoint" in info["default_values"]
        assert "DummyConverter.target" in info["default_values"]
        assert len(info["default_values"]) == 2

        # Verify global_variables tracking - should list variable names
        assert isinstance(info["global_variables"], list)
        assert "test_global_var" in info["global_variables"]
        assert "test_converter_target" in info["global_variables"]
        assert len(info["global_variables"]) == 2

    async def test_get_info_empty_when_nothing_set(self):
        """Test that get_info returns empty lists when initializer sets nothing."""

        class EmptyInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Empty Tracker"

            @property
            def description(self) -> str:
                return "Sets nothing"

            async def initialize_async(self) -> None:
                pass  # Don't set anything

        info = await EmptyInit.get_info_async()

        # Should have empty lists, not error messages
        assert isinstance(info["default_values"], list)
        assert isinstance(info["global_variables"], list)
        assert len(info["default_values"]) == 0
        assert len(info["global_variables"]) == 0


@pytest.mark.usefixtures("patch_central_database")
class TestGetDynamicDefaultValuesInfo:
    """Tests for get_dynamic_default_values_info_async method."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()

    async def test_get_dynamic_info_returns_dict(self):
        """Test that method returns a dictionary."""

        class DynamicInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Dynamic"

            @property
            def description(self) -> str:
                return "Dynamic info"

            async def initialize_async(self) -> None:
                pass

        init = DynamicInit()
        info = await init.get_dynamic_default_values_info_async()
        assert isinstance(info, dict)

    async def test_get_dynamic_info_has_required_keys(self):
        """Test that returned dict has required keys."""

        class KeysInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Keys"

            @property
            def description(self) -> str:
                return "For keys test"

            async def initialize_async(self) -> None:
                pass

        init = KeysInit()
        info = await init.get_dynamic_default_values_info_async()
        assert "default_values" in info
        assert "global_variables" in info

    async def test_get_dynamic_info_captures_defaults(self):
        """Test that method captures default values set during init."""

        class DummyClass:
            def __init__(self, *, value: str = "default") -> None:
                self.value = value

        class DefaultsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Defaults capture"

            @property
            def description(self) -> str:
                return "Captures defaults"

            async def initialize_async(self) -> None:
                set_default_value(class_type=DummyClass, parameter_name="value", value="captured")

        init = DefaultsInit()
        info = await init.get_dynamic_default_values_info_async()

        # Should capture that a default was set
        assert isinstance(info["default_values"], list)

    async def test_get_dynamic_info_captures_globals(self):
        """Test that method captures global variables."""

        class GlobalsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Globals capture"

            @property
            def description(self) -> str:
                return "Captures globals"

            async def initialize_async(self) -> None:
                set_global_variable(name="dynamic_test_var", value="captured")

        init = GlobalsInit()
        info = await init.get_dynamic_default_values_info_async()

        assert isinstance(info["global_variables"], list)

    async def test_get_dynamic_info_restores_state(self):
        """Test that method restores original state after sandbox run."""

        class DummyClass:
            def __init__(self, *, value: str = "default") -> None:
                self.value = value

        # Set an initial default
        set_default_value(class_type=DummyClass, parameter_name="value", value="original")

        class RestoringInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "Restoring"

            @property
            def description(self) -> str:
                return "Restores state"

            async def initialize_async(self) -> None:
                set_default_value(class_type=DummyClass, parameter_name="other_value", value="temporary")

        init = RestoringInit()
        await init.get_dynamic_default_values_info_async()

        # Original default should still be there
        from pyrit.common.apply_defaults import get_global_default_values

        registry = get_global_default_values()
        # Should only have the original default, not the temporary one
        assert len(registry._default_values) == 1


class TestGetDynamicDefaultValuesInfoWithoutMemory:
    """Tests for get_dynamic_default_values_info_async method without memory."""

    async def test_get_dynamic_info_without_memory_returns_message(self):
        """Test that method returns helpful message when memory not initialized."""
        from pyrit.memory import CentralMemory

        # Ensure memory is not set
        CentralMemory.set_memory_instance(None)  # type: ignore[arg-type]

        class NoMemoryInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "No memory"

            @property
            def description(self) -> str:
                return "No memory initialized"

            async def initialize_async(self) -> None:
                pass

        init = NoMemoryInit()
        info = await init.get_dynamic_default_values_info_async()

        # Should return helpful messages
        assert "await initialize_pyrit_async()" in str(info["default_values"])
        assert "await initialize_pyrit_async()" in str(info["global_variables"])


class TestSupportedParameters:
    """Tests for the parameter system on PyRITInitializer."""

    def test_default_supported_parameters_is_empty(self) -> None:
        """Test that base class returns empty supported_parameters."""

        class NoParamsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "no_params"

            async def initialize_async(self) -> None:
                pass

        init = NoParamsInit()
        assert init.supported_parameters == []

    def test_supported_parameters_declared(self) -> None:
        """Test that subclass can declare supported_parameters."""

        class WithParamsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "with_params"

            @property
            def supported_parameters(self) -> list:
                return [
                    InitializerParameter(name="mode", description="Operation mode", default="fast"),
                    InitializerParameter(name="count", description="Item count", required=True),
                ]

            async def initialize_async(self) -> None:
                pass

        init = WithParamsInit()
        assert len(init.supported_parameters) == 2
        assert init.supported_parameters[0].name == "mode"
        assert init.supported_parameters[1].required is True

    def test_validate_params_raises_on_unknown(self) -> None:
        """Test that unknown params raise ValueError."""

        class StrictInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "strict"

            @property
            def supported_parameters(self) -> list:
                return [InitializerParameter(name="level", description="Level")]

            async def initialize_async(self) -> None:
                pass

        init = StrictInit()
        with pytest.raises(ValueError, match="unknown parameter"):
            init._validate_params(params={"bogus": ["value"]})

    def test_validate_params_raises_on_missing_required(self) -> None:
        """Test that missing required params raise ValueError."""

        class RequiredInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "required"

            @property
            def supported_parameters(self) -> list:
                return [InitializerParameter(name="key", description="API key", required=True)]

            async def initialize_async(self) -> None:
                pass

        init = RequiredInit()
        with pytest.raises(ValueError, match="requires parameter 'key'"):
            init._validate_params(params={})

    def test_validate_params_accepts_valid(self) -> None:
        """Test that valid params pass validation."""

        class ValidInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "valid"

            @property
            def supported_parameters(self) -> list:
                return [
                    InitializerParameter(name="mode", description="Mode", default="fast"),
                    InitializerParameter(name="key", description="Key", required=True),
                ]

            async def initialize_async(self) -> None:
                pass

        init = ValidInit()
        # Should not raise
        init._validate_params(params={"key": ["abc"], "mode": ["slow"]})

    def test_validate_checks_params_on_instance(self) -> None:
        """Test that validate() checks self.params."""

        class ParamInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "param_init"

            @property
            def supported_parameters(self) -> list:
                return [InitializerParameter(name="x", description="X")]

            async def initialize_async(self) -> None:
                pass

        init = ParamInit()
        init.params = {"unknown_key": ["val"]}
        with pytest.raises(ValueError, match="unknown parameter"):
            init.validate()

    async def test_params_available_in_initialize_async(self) -> None:
        """Test that self.params is available inside initialize_async."""

        received_params = {}

        class TrackingInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "tracking"

            async def initialize_async(self) -> None:
                received_params.update(self.params)

        init = TrackingInit()
        init.params = {"tags": ["default", "scorer"]}
        await init.initialize_with_tracking_async()

        assert received_params == {"tags": ["default", "scorer"]}

    async def test_empty_params_remains_empty_dict(self) -> None:
        """Test that self.params is empty dict when not set."""

        received = {"params": "not_set"}

        class EmptyParamsInit(PyRITInitializer):
            @property
            def name(self) -> str:
                return "empty"

            async def initialize_async(self) -> None:
                received["params"] = dict(self.params)

        init = EmptyParamsInit()
        await init.initialize_with_tracking_async()

        assert received["params"] == {}
