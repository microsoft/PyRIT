# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

import pytest

from pyrit.common.apply_defaults import (
    REQUIRED_VALUE,
    DefaultValueScope,
    apply_defaults,
    get_global_default_values,
    reset_default_values,
    set_default_value,
    set_global_variable,
)


class TestApplyDefaultsDecorator:
    """Tests for the @apply_defaults decorator."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_no_defaults_configured_returns_none(self) -> None:
        """Test that parameters remain None when no defaults are configured."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                self.param1 = param1
                self.param2 = param2

        obj = TestClass()
        assert obj.param1 is None
        assert obj.param2 is None

    def test_single_default_value_applied(self) -> None:
        """Test that a single default value is applied correctly."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None) -> None:
                self.param1 = param1

        set_default_value(class_type=TestClass, parameter_name="param1", value="default_value")

        obj = TestClass()
        assert obj.param1 == "default_value"

    def test_multiple_default_values_applied(self) -> None:
        """Test that multiple default values are applied correctly."""

        class TestClass:
            @apply_defaults
            def __init__(
                self, *, param1: Optional[str] = None, param2: Optional[int] = None, param3: Optional[float] = None
            ) -> None:
                self.param1 = param1
                self.param2 = param2
                self.param3 = param3

        set_default_value(class_type=TestClass, parameter_name="param1", value="test")
        set_default_value(class_type=TestClass, parameter_name="param2", value=42)
        set_default_value(class_type=TestClass, parameter_name="param3", value=3.14)

        obj = TestClass()
        assert obj.param1 == "test"
        assert obj.param2 == 42
        assert obj.param3 == 3.14

    def test_explicit_value_overrides_default(self) -> None:
        """Test that explicitly provided values override defaults."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                self.param1 = param1
                self.param2 = param2

        set_default_value(class_type=TestClass, parameter_name="param1", value="default")
        set_default_value(class_type=TestClass, parameter_name="param2", value=100)

        obj = TestClass(param1="explicit", param2=200)
        assert obj.param1 == "explicit"
        assert obj.param2 == 200

    def test_partial_override_uses_remaining_defaults(self) -> None:
        """Test that overriding some values still uses defaults for others."""

        class TestClass:
            @apply_defaults
            def __init__(
                self, *, param1: Optional[str] = None, param2: Optional[int] = None, param3: Optional[float] = None
            ) -> None:
                self.param1 = param1
                self.param2 = param2
                self.param3 = param3

        set_default_value(class_type=TestClass, parameter_name="param1", value="default1")
        set_default_value(class_type=TestClass, parameter_name="param2", value=100)
        set_default_value(class_type=TestClass, parameter_name="param3", value=1.5)

        obj = TestClass(param2=200)
        assert obj.param1 == "default1"
        assert obj.param2 == 200
        assert obj.param3 == 1.5

    def test_falsy_values_are_not_overridden(self) -> None:
        """Test that falsy values (0, False, "") are preserved and not treated as None."""

        class TestClass:
            @apply_defaults
            def __init__(
                self,
                *,
                param_int: Optional[int] = None,
                param_bool: Optional[bool] = None,
                param_str: Optional[str] = None,
            ) -> None:
                self.param_int = param_int
                self.param_bool = param_bool
                self.param_str = param_str

        set_default_value(class_type=TestClass, parameter_name="param_int", value=100)
        set_default_value(class_type=TestClass, parameter_name="param_bool", value=True)
        set_default_value(class_type=TestClass, parameter_name="param_str", value="default")

        obj = TestClass(param_int=0, param_bool=False, param_str="")
        assert obj.param_int == 0
        assert obj.param_bool is False
        assert obj.param_str == ""


class TestInheritance:
    """Tests for default value inheritance behavior."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_subclass_inherits_parent_defaults(self) -> None:
        """Test that subclass inherits defaults from parent class."""

        class ParentClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                self.param1 = param1
                self.param2 = param2

        class ChildClass(ParentClass):
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                super().__init__(param1=param1, param2=param2)

        set_default_value(class_type=ParentClass, parameter_name="param1", value="parent_value")
        set_default_value(class_type=ParentClass, parameter_name="param2", value=42)

        child_obj = ChildClass()
        assert child_obj.param1 == "parent_value"
        assert child_obj.param2 == 42

    def test_subclass_specific_defaults_override_parent(self) -> None:
        """Test that subclass-specific defaults take precedence over parent defaults."""

        class ParentClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                self.param1 = param1
                self.param2 = param2

        class ChildClass(ParentClass):
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                super().__init__(param1=param1, param2=param2)

        set_default_value(class_type=ParentClass, parameter_name="param1", value="parent_value")
        set_default_value(class_type=ParentClass, parameter_name="param2", value=100)

        set_default_value(class_type=ChildClass, parameter_name="param1", value="child_value")

        child_obj = ChildClass()
        assert child_obj.param1 == "child_value"  # Child overrides parent
        assert child_obj.param2 == 100  # Inherited from parent

    def test_multiple_inheritance_levels(self) -> None:
        """Test defaults work correctly with multiple levels of inheritance."""

        class GrandParent:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None) -> None:
                self.param1 = param1

        class Parent(GrandParent):
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                super().__init__(param1=param1)
                self.param2 = param2

        class Child(Parent):
            @apply_defaults
            def __init__(
                self, *, param1: Optional[str] = None, param2: Optional[int] = None, param3: Optional[float] = None
            ) -> None:
                super().__init__(param1=param1, param2=param2)
                self.param3 = param3

        set_default_value(class_type=GrandParent, parameter_name="param1", value="grandparent")
        set_default_value(class_type=Parent, parameter_name="param2", value=50)
        set_default_value(class_type=Child, parameter_name="param3", value=3.14)

        child_obj = Child()
        assert child_obj.param1 == "grandparent"
        assert child_obj.param2 == 50
        assert child_obj.param3 == 3.14

    def test_parent_not_affected_by_child_defaults(self) -> None:
        """Test that setting defaults on child class doesn't affect parent instances."""

        class ParentClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None) -> None:
                self.param1 = param1

        class ChildClass(ParentClass):
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None) -> None:
                super().__init__(param1=param1)

        set_default_value(class_type=ChildClass, parameter_name="param1", value="child_value")

        parent_obj = ParentClass()
        assert parent_obj.param1 is None  # Parent doesn't get child's default


class TestDefaultValueScope:
    """Tests for DefaultValueScope dataclass."""

    def test_default_value_scope_is_hashable(self) -> None:
        """Test that DefaultValueScope can be used as a dictionary key."""

        class TestClass:
            pass

        scope1 = DefaultValueScope(parameter_name="test", class_type=TestClass)
        scope2 = DefaultValueScope(parameter_name="test", class_type=TestClass)

        # Should be able to use as dict key
        test_dict = {scope1: "value1"}
        assert test_dict[scope2] == "value1"  # Same scope should retrieve same value

    def test_default_value_scope_equality(self) -> None:
        """Test that scopes with same values are equal."""

        class TestClass:
            pass

        scope1 = DefaultValueScope(parameter_name="test", class_type=TestClass)
        scope2 = DefaultValueScope(parameter_name="test", class_type=TestClass)

        assert scope1 == scope2

    def test_default_value_scope_inequality(self) -> None:
        """Test that scopes with different values are not equal."""

        class TestClass1:
            pass

        class TestClass2:
            pass

        scope1 = DefaultValueScope(parameter_name="test", class_type=TestClass1)
        scope2 = DefaultValueScope(parameter_name="test", class_type=TestClass2)
        scope3 = DefaultValueScope(parameter_name="other", class_type=TestClass1)

        assert scope1 != scope2
        assert scope1 != scope3


class TestPyRITDefaultValues:
    """Tests for the PyRITDefaultValues class."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_get_default_value_returns_configured_value(self) -> None:
        """Test that get_default_value returns configured values."""

        class TestClass:
            pass

        defaults = get_global_default_values()
        defaults.set_default_value(class_type=TestClass, parameter_name="test", value="test_value")

        found, result = defaults.get_default_value(class_type=TestClass, parameter_name="test")
        assert found is True
        assert result == "test_value"

    def test_get_default_value_returns_fallback_when_not_configured(self) -> None:
        """Test that get_default_value returns fallback when no value is configured."""

        class TestClass:
            pass

        defaults = get_global_default_values()

        found, result = defaults.get_default_value(class_type=TestClass, parameter_name="test")
        assert found is False
        assert result is None

    def test_get_default_value_for_parameter_with_provided_value(self) -> None:
        """Test that provided values are returned regardless of defaults."""

        class TestClass:
            pass

        set_default_value(class_type=TestClass, parameter_name="test", value="default")
        defaults = get_global_default_values()

        # When a value is provided, it should be used (not the default)
        found, result = defaults.get_default_value(class_type=TestClass, parameter_name="test")
        # The default is set, so we should find it
        assert found is True
        assert result == "default"

        # But if we explicitly provide a value, that takes precedence
        # (this is tested via @apply_defaults decorator tests)

    def test_get_default_value_for_parameter_without_provided_value(self) -> None:
        """Test that default value is returned when provided value is None."""

        class TestClass:
            pass

        set_default_value(class_type=TestClass, parameter_name="test", value="default")
        defaults = get_global_default_values()

        found, result = defaults.get_default_value(class_type=TestClass, parameter_name="test")
        assert found is True
        assert result == "default"


class TestSetDefaultValue:
    """Tests for the set_default_value convenience function."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_set_default_value_stores_value(self) -> None:
        """Test that set_default_value properly stores values."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None) -> None:
                self.param1 = param1

        set_default_value(class_type=TestClass, parameter_name="param1", value="stored_value")

        obj = TestClass()
        assert obj.param1 == "stored_value"

    def test_set_default_value_overwrites_existing(self) -> None:
        """Test that setting a default value overwrites any existing value."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None) -> None:
                self.param1 = param1

        set_default_value(class_type=TestClass, parameter_name="param1", value="first_value")
        set_default_value(class_type=TestClass, parameter_name="param1", value="second_value")

        obj = TestClass()
        assert obj.param1 == "second_value"


class TestComplexScenarios:
    """Tests for complex real-world scenarios."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_openai_chat_target_scenario(self) -> None:
        """Test a realistic scenario similar to OpenAIChatTarget."""

        class OpenAIChatTarget:
            @apply_defaults
            def __init__(
                self,
                *,
                temperature: Optional[float] = None,
                top_p: Optional[float] = None,
                max_tokens: Optional[int] = None,
            ) -> None:
                self.temperature = temperature
                self.top_p = top_p
                self.max_tokens = max_tokens

        class AzureOpenAIChatTarget(OpenAIChatTarget):
            @apply_defaults
            def __init__(
                self,
                *,
                temperature: Optional[float] = None,
                top_p: Optional[float] = None,
                max_tokens: Optional[int] = None,
            ) -> None:
                super().__init__(temperature=temperature, top_p=top_p, max_tokens=max_tokens)

        # Set defaults for base class
        set_default_value(class_type=OpenAIChatTarget, parameter_name="temperature", value=0.7)
        set_default_value(class_type=OpenAIChatTarget, parameter_name="top_p", value=0.9)

        # Set defaults for subclass (more specific temperature)
        set_default_value(class_type=AzureOpenAIChatTarget, parameter_name="temperature", value=0.3)

        # Test base class
        base_obj = OpenAIChatTarget()
        assert base_obj.temperature == 0.7
        assert base_obj.top_p == 0.9
        assert base_obj.max_tokens is None

        # Test subclass with inheritance
        azure_obj = AzureOpenAIChatTarget()
        assert azure_obj.temperature == 0.3  # More specific default
        assert azure_obj.top_p == 0.9  # Inherited from parent
        assert azure_obj.max_tokens is None  # No default set

        # Test with explicit overrides
        custom_obj = AzureOpenAIChatTarget(temperature=0.5, max_tokens=100)
        assert custom_obj.temperature == 0.5  # Explicit override
        assert custom_obj.top_p == 0.9  # Still uses default
        assert custom_obj.max_tokens == 100  # Explicit override

    def test_multiple_classes_independent_defaults(self) -> None:
        """Test that multiple classes can have independent default configurations."""

        class ClassA:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        class ClassB:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        set_default_value(class_type=ClassA, parameter_name="param", value="value_a")
        set_default_value(class_type=ClassB, parameter_name="param", value="value_b")

        obj_a = ClassA()
        obj_b = ClassB()

        assert obj_a.param == "value_a"
        assert obj_b.param == "value_b"


class TestResetDefaultValues:
    """Tests for the reset_default_values() function."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_reset_clears_all_defaults(self) -> None:
        """Test that reset_default_values() clears all configured defaults."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[int] = None) -> None:
                self.param1 = param1
                self.param2 = param2

        # Set some defaults
        set_default_value(class_type=TestClass, parameter_name="param1", value="default")
        set_default_value(class_type=TestClass, parameter_name="param2", value=42)

        # Verify defaults are applied
        obj1 = TestClass()
        assert obj1.param1 == "default"
        assert obj1.param2 == 42

        # Reset all defaults
        reset_default_values()

        # Verify defaults are no longer applied
        obj2 = TestClass()
        assert obj2.param1 is None
        assert obj2.param2 is None

    def test_reset_affects_multiple_classes(self) -> None:
        """Test that reset_default_values() clears defaults for all classes."""

        class ClassA:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        class ClassB:
            @apply_defaults
            def __init__(self, *, param: Optional[int] = None) -> None:
                self.param = param

        # Set defaults for multiple classes
        set_default_value(class_type=ClassA, parameter_name="param", value="class_a_default")
        set_default_value(class_type=ClassB, parameter_name="param", value=100)

        # Reset all defaults
        reset_default_values()

        # Verify both classes have no defaults
        obj_a = ClassA()
        obj_b = ClassB()
        assert obj_a.param is None
        assert obj_b.param is None

    def test_reset_allows_setting_new_defaults(self) -> None:
        """Test that after reset, new defaults can be set and applied correctly."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        # Set initial default
        set_default_value(class_type=TestClass, parameter_name="param", value="first_default")
        obj1 = TestClass()
        assert obj1.param == "first_default"

        # Reset and set new default
        reset_default_values()
        set_default_value(class_type=TestClass, parameter_name="param", value="second_default")

        # Verify new default is applied
        obj2 = TestClass()
        assert obj2.param == "second_default"

    def test_reset_with_no_defaults_does_nothing(self) -> None:
        """Test that reset_default_values() can be called safely when no defaults exist."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        # Reset when no defaults are set
        reset_default_values()

        # Verify class still works normally
        obj = TestClass()
        assert obj.param is None

        obj2 = TestClass(param="explicit")
        assert obj2.param == "explicit"

    def test_reset_clears_inheritance_based_defaults(self) -> None:
        """Test that reset clears defaults for both parent and child classes."""

        class ParentClass:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        class ChildClass(ParentClass):
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                super().__init__(param=param)

        # Set defaults for both parent and child
        set_default_value(class_type=ParentClass, parameter_name="param", value="parent_default")
        set_default_value(class_type=ChildClass, parameter_name="param", value="child_default")

        # Reset all defaults
        reset_default_values()

        # Verify both parent and child have no defaults
        parent_obj = ParentClass()
        child_obj = ChildClass()
        assert parent_obj.param is None
        assert child_obj.param is None

    def test_reset_clears_include_subclasses_flag_variations(self) -> None:
        """Test that reset clears defaults regardless of include_subclasses flag."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, param1: Optional[str] = None, param2: Optional[str] = None) -> None:
                self.param1 = param1
                self.param2 = param2

        # Set defaults with different include_subclasses values
        set_default_value(class_type=TestClass, parameter_name="param1", value="default1", include_subclasses=True)
        set_default_value(class_type=TestClass, parameter_name="param2", value="default2", include_subclasses=False)

        # Reset all defaults
        reset_default_values()

        # Verify both are cleared
        obj = TestClass()
        assert obj.param1 is None
        assert obj.param2 is None


class TestSetGlobalVariable:
    """Tests for the set_global_variable function."""

    def test_set_global_variable_creates_variable(self) -> None:
        """Test that set_global_variable creates a variable in __main__ namespace."""
        import sys

        # Ensure the variable doesn't exist initially
        if hasattr(sys.modules["__main__"], "test_global_var"):
            delattr(sys.modules["__main__"], "test_global_var")

        try:
            # Set a global variable
            set_global_variable(name="test_global_var", value="test_value")

            # Verify it exists in __main__ namespace
            assert hasattr(sys.modules["__main__"], "test_global_var")
            assert sys.modules["__main__"].test_global_var == "test_value"  # type: ignore[attr-defined]

        finally:
            # Cleanup
            if hasattr(sys.modules["__main__"], "test_global_var"):
                delattr(sys.modules["__main__"], "test_global_var")

    def test_set_global_variable_overwrites_existing(self) -> None:
        """Test that set_global_variable overwrites existing variables."""
        import sys

        try:
            # Set initial value
            set_global_variable(name="test_overwrite_var", value="initial_value")
            assert sys.modules["__main__"].test_overwrite_var == "initial_value"  # type: ignore[attr-defined]

            # Overwrite with new value
            set_global_variable(name="test_overwrite_var", value="new_value")
            assert sys.modules["__main__"].test_overwrite_var == "new_value"  # type: ignore[attr-defined]

        finally:
            # Cleanup
            if hasattr(sys.modules["__main__"], "test_overwrite_var"):
                delattr(sys.modules["__main__"], "test_overwrite_var")

    def test_set_global_variable_with_complex_objects(self) -> None:
        """Test that set_global_variable works with complex objects."""
        import sys

        try:
            # Test with a dictionary
            test_dict = {"key1": "value1", "key2": [1, 2, 3]}
            set_global_variable(name="test_dict_var", value=test_dict)

            assert hasattr(sys.modules["__main__"], "test_dict_var")
            assert sys.modules["__main__"].test_dict_var == test_dict  # type: ignore[attr-defined]
            assert sys.modules["__main__"].test_dict_var["key1"] == "value1"  # type: ignore[attr-defined]

            # Test with a class instance
            class TestClass:
                def __init__(self, *, value: str) -> None:
                    self.value = value

            test_obj = TestClass(value="test_instance")
            set_global_variable(name="test_obj_var", value=test_obj)

            assert hasattr(sys.modules["__main__"], "test_obj_var")
            assert sys.modules["__main__"].test_obj_var.value == "test_instance"  # type: ignore[attr-defined]

        finally:
            # Cleanup
            if hasattr(sys.modules["__main__"], "test_dict_var"):
                delattr(sys.modules["__main__"], "test_dict_var")
            if hasattr(sys.modules["__main__"], "test_obj_var"):
                delattr(sys.modules["__main__"], "test_obj_var")

    def test_set_global_variable_with_none_value(self) -> None:
        """Test that set_global_variable can set None as a value."""
        import sys

        try:
            set_global_variable(name="test_none_var", value=None)

            assert hasattr(sys.modules["__main__"], "test_none_var")
            assert sys.modules["__main__"].test_none_var is None  # type: ignore[attr-defined]

        finally:
            # Cleanup
            if hasattr(sys.modules["__main__"], "test_none_var"):
                delattr(sys.modules["__main__"], "test_none_var")


class TestRequiredValue:
    """Tests for the REQUIRED_VALUE sentinel."""

    def setup_method(self) -> None:
        """Clear any existing default values before each test."""
        get_global_default_values()._default_values.clear()

    def test_required_value_raises_when_not_provided_and_no_default(self) -> None:
        """Test that REQUIRED_VALUE raises ValueError when parameter not provided and no default set."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        with pytest.raises(ValueError) as exc_info:
            TestClass()

        assert "required_param" in str(exc_info.value)
        assert "is required" in str(exc_info.value)

    def test_required_value_uses_default_when_provided(self) -> None:
        """Test that REQUIRED_VALUE uses global default when registered."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        set_default_value(class_type=TestClass, parameter_name="required_param", value="default_value")

        obj = TestClass()
        assert obj.required_param == "default_value"

    def test_required_value_accepts_explicit_value(self) -> None:
        """Test that REQUIRED_VALUE accepts explicitly provided value."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        obj = TestClass(required_param="explicit_value")
        assert obj.required_param == "explicit_value"

    def test_required_value_explicit_overrides_default(self) -> None:
        """Test that explicit value overrides registered default even with REQUIRED_VALUE."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        set_default_value(class_type=TestClass, parameter_name="required_param", value="default_value")

        obj = TestClass(required_param="explicit_value")
        assert obj.required_param == "explicit_value"

    def test_required_value_with_multiple_params(self) -> None:
        """Test REQUIRED_VALUE works alongside optional parameters."""

        class TestClass:
            @apply_defaults
            def __init__(
                self,
                *,
                required_param: str = REQUIRED_VALUE,  # type: ignore[assignment]
                optional_param: Optional[str] = None,
            ) -> None:
                self.required_param = required_param
                self.optional_param = optional_param

        # Should raise for missing required param
        with pytest.raises(ValueError) as exc_info:
            TestClass(optional_param="optional")
        assert "required_param" in str(exc_info.value)

        # Should work with required param provided
        obj = TestClass(required_param="required", optional_param="optional")
        assert obj.required_param == "required"
        assert obj.optional_param == "optional"

    def test_required_value_with_inheritance(self) -> None:
        """Test that REQUIRED_VALUE works with class inheritance."""

        class ParentClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        class ChildClass(ParentClass):
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                super().__init__(required_param=required_param)

        # Set default for parent class
        set_default_value(class_type=ParentClass, parameter_name="required_param", value="parent_default")

        # Child should inherit parent's default
        obj = ChildClass()
        assert obj.required_param == "parent_default"

    def test_required_value_child_default_overrides_parent(self) -> None:
        """Test that child class default overrides parent default with REQUIRED_VALUE."""

        class ParentClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        class ChildClass(ParentClass):
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                super().__init__(required_param=required_param)

        set_default_value(class_type=ParentClass, parameter_name="required_param", value="parent_default")
        set_default_value(class_type=ChildClass, parameter_name="required_param", value="child_default")

        obj = ChildClass()
        assert obj.required_param == "child_default"

    def test_required_value_sentinel_properties(self) -> None:
        """Test the properties of REQUIRED_VALUE sentinel."""
        # REQUIRED_VALUE should be falsy
        assert not REQUIRED_VALUE

        # REQUIRED_VALUE should have a readable repr
        assert "REQUIRED_VALUE" in repr(REQUIRED_VALUE)

    def test_required_value_complex_type(self) -> None:
        """Test REQUIRED_VALUE with complex types like objects."""

        class ComplexType:
            def __init__(self, value: str):
                self.value = value

        class TestClass:
            @apply_defaults
            def __init__(self, *, required_param: ComplexType = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        complex_obj = ComplexType("test")
        set_default_value(class_type=TestClass, parameter_name="required_param", value=complex_obj)

        obj = TestClass()
        assert obj.required_param is complex_obj
        assert obj.required_param.value == "test"

    def test_required_value_none_is_different(self) -> None:
        """Test that None and REQUIRED_VALUE behave differently."""

        class TestClass1:
            @apply_defaults
            def __init__(self, *, param: Optional[str] = None) -> None:
                self.param = param

        class TestClass2:
            @apply_defaults
            def __init__(self, *, param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.param = param

        # With None default, no error even without registered default
        obj1 = TestClass1()
        assert obj1.param is None

        # With REQUIRED_VALUE, raises error without registered default
        with pytest.raises(ValueError):
            TestClass2()

    def test_required_value_raises_when_none_explicitly_passed(self) -> None:
        """Test that REQUIRED_VALUE raises ValueError when None is explicitly passed."""

        class TestClass:
            @apply_defaults
            def __init__(self, *, required_param: str = REQUIRED_VALUE) -> None:  # type: ignore[assignment]
                self.required_param = required_param

        # Explicitly passing None should raise error
        with pytest.raises(ValueError) as exc_info:
            TestClass(required_param=None)  # type: ignore[arg-type]

        assert "required_param" in str(exc_info.value)
        assert "is required" in str(exc_info.value)
