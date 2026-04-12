# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.common.apply_defaults import (
    REQUIRED_VALUE,
    DefaultValueScope,
    GlobalDefaultValues,
    apply_defaults,
    get_global_default_values,
    reset_default_values,
    set_default_value,
    set_global_variable,
)


@pytest.fixture(autouse=True)
def _reset_defaults():
    reset_default_values()
    yield
    reset_default_values()


class _Base:
    @apply_defaults
    def __init__(self, *, name: str | None = None, count: int = 5) -> None:
        self.name = name
        self.count = count


class _Child(_Base):
    pass


class _WithRequired:
    @apply_defaults
    def __init__(self, *, target: str = REQUIRED_VALUE) -> None:
        self.target = target


# --- _RequiredValueSentinel ---


def test_required_value_sentinel_repr():
    assert repr(REQUIRED_VALUE) == "REQUIRED_VALUE"


def test_required_value_sentinel_is_falsy():
    assert not REQUIRED_VALUE


# --- DefaultValueScope ---


def test_default_value_scope_hash_equal():
    s1 = DefaultValueScope(class_type=_Base, parameter_name="name", include_subclasses=True)
    s2 = DefaultValueScope(class_type=_Base, parameter_name="name", include_subclasses=True)
    assert hash(s1) == hash(s2)


def test_default_value_scope_hash_differs_on_param():
    s1 = DefaultValueScope(class_type=_Base, parameter_name="name")
    s2 = DefaultValueScope(class_type=_Base, parameter_name="count")
    assert hash(s1) != hash(s2)


# --- GlobalDefaultValues ---


def test_global_default_values_set_and_get():
    registry = GlobalDefaultValues()
    registry.set_default_value(class_type=_Base, parameter_name="name", value="hello")
    found, val = registry.get_default_value(class_type=_Base, parameter_name="name")
    assert found is True
    assert val == "hello"


def test_global_default_values_not_found():
    registry = GlobalDefaultValues()
    found, val = registry.get_default_value(class_type=_Base, parameter_name="name")
    assert found is False
    assert val is None


def test_global_default_values_subclass_inheritance():
    registry = GlobalDefaultValues()
    registry.set_default_value(class_type=_Base, parameter_name="name", value="inherited")
    found, val = registry.get_default_value(class_type=_Child, parameter_name="name")
    assert found is True
    assert val == "inherited"


def test_global_default_values_no_subclass_when_disabled():
    registry = GlobalDefaultValues()
    registry.set_default_value(class_type=_Base, parameter_name="name", value="no-inherit", include_subclasses=False)
    found, val = registry.get_default_value(class_type=_Child, parameter_name="name")
    assert found is False


def test_global_default_values_reset():
    registry = GlobalDefaultValues()
    registry.set_default_value(class_type=_Base, parameter_name="name", value="x")
    registry.reset_defaults()
    assert registry.all_defaults == {}


def test_global_default_values_all_defaults_returns_copy():
    registry = GlobalDefaultValues()
    registry.set_default_value(class_type=_Base, parameter_name="name", value="x")
    copy = registry.all_defaults
    copy.clear()
    assert len(registry.all_defaults) == 1


# --- Module-level helpers ---


def test_get_global_default_values_returns_instance():
    assert isinstance(get_global_default_values(), GlobalDefaultValues)


def test_set_default_value_module_function():
    set_default_value(class_type=_Base, parameter_name="name", value="mod")
    found, val = get_global_default_values().get_default_value(class_type=_Base, parameter_name="name")
    assert found is True
    assert val == "mod"


def test_reset_default_values_clears():
    set_default_value(class_type=_Base, parameter_name="name", value="clear")
    reset_default_values()
    found, _ = get_global_default_values().get_default_value(class_type=_Base, parameter_name="name")
    assert found is False


def test_set_global_variable():
    import sys

    set_global_variable(name="_test_sentinel_var", value=42)
    assert sys.modules["__main__"].__dict__["_test_sentinel_var"] == 42
    del sys.modules["__main__"].__dict__["_test_sentinel_var"]


# --- @apply_defaults decorator ---


def test_apply_defaults_uses_explicit_args():
    obj = _Base(name="explicit", count=10)
    assert obj.name == "explicit"
    assert obj.count == 10


def test_apply_defaults_uses_registered_default_when_none():
    set_default_value(class_type=_Base, parameter_name="name", value="default_name")
    obj = _Base()
    assert obj.name == "default_name"


def test_apply_defaults_explicit_overrides_registered():
    set_default_value(class_type=_Base, parameter_name="name", value="default_name")
    obj = _Base(name="explicit")
    assert obj.name == "explicit"


def test_apply_defaults_inherits_to_subclass():
    set_default_value(class_type=_Base, parameter_name="name", value="parent_default")
    obj = _Child()
    assert obj.name == "parent_default"


def test_apply_defaults_required_value_raises_when_missing():
    with pytest.raises(ValueError, match="target is required"):
        _WithRequired()


def test_apply_defaults_required_value_satisfied_by_registered():
    set_default_value(class_type=_WithRequired, parameter_name="target", value="registered")
    obj = _WithRequired()
    assert obj.target == "registered"


def test_apply_defaults_required_value_satisfied_by_explicit():
    obj = _WithRequired(target="explicit")
    assert obj.target == "explicit"


def test_apply_defaults_none_on_required_value_param_raises():
    with pytest.raises(ValueError, match="target is required"):
        _WithRequired(target=None)
