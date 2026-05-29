# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
import types
import warnings

from pyrit.common.deprecation import (
    deprecated_kwarg,
    module_deprecation_getattr,
    print_deprecation_message,
)


def _old_func():
    pass


def _new_func():
    pass


class _OldClass:
    pass


class _NewClass:
    pass


def test_deprecation_warning_with_callables():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item=_old_func, new_item=_new_func, removed_in="2.0")
    assert len(w) == 1
    assert issubclass(w[0].category, DeprecationWarning)
    assert "_old_func" in str(w[0].message)
    assert "_new_func" in str(w[0].message)
    assert "2.0" in str(w[0].message)


def test_deprecation_warning_with_classes():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item=_OldClass, new_item=_NewClass, removed_in="3.0")
    assert len(w) == 1
    assert "_OldClass" in str(w[0].message)
    assert "_NewClass" in str(w[0].message)
    assert "3.0" in str(w[0].message)


def test_deprecation_warning_with_strings():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item="OldName", new_item="NewName", removed_in="4.0")
    assert len(w) == 1
    assert "OldName" in str(w[0].message)
    assert "NewName" in str(w[0].message)


def test_deprecation_warning_mixed_types():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item=_OldClass, new_item="some.new.path", removed_in="5.0")
    assert len(w) == 1
    assert "_OldClass" in str(w[0].message)
    assert "some.new.path" in str(w[0].message)


# --- deprecated_kwarg ----------------------------------------------------


def test_deprecated_kwarg_promotes_old_to_new():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = deprecated_kwarg(
            {"old": 42},
            old_name="old",
            new_name="new",
            removed_in="9.9",
            model="ExampleModel",
        )
    assert result == {"new": 42}
    assert len(w) == 1
    assert issubclass(w[0].category, DeprecationWarning)
    assert "old" in str(w[0].message)
    assert "new" in str(w[0].message)
    assert "ExampleModel" in str(w[0].message)
    assert "9.9" in str(w[0].message)


def test_deprecated_kwarg_noop_when_only_new_set():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = deprecated_kwarg(
            {"new": 42},
            old_name="old",
            new_name="new",
            removed_in="9.9",
            model="ExampleModel",
        )
    assert result == {"new": 42}
    assert len(w) == 0


def test_deprecated_kwarg_does_not_overwrite_new_when_both_set():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = deprecated_kwarg(
            {"old": 42, "new": 7},
            old_name="old",
            new_name="new",
            removed_in="9.9",
            model="ExampleModel",
        )
    assert result == {"new": 7}
    assert len(w) == 1


def test_deprecated_kwarg_passes_through_non_dict():
    sentinel = object()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = deprecated_kwarg(
            sentinel,
            old_name="old",
            new_name="new",
            removed_in="9.9",
            model="ExampleModel",
        )
    assert result is sentinel
    assert len(w) == 0


# --- module_deprecation_getattr ------------------------------------------


def _make_target_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.exposed_value = 123
    module.another_value = "hello"
    sys.modules[name] = module
    return module


def test_module_deprecation_getattr_resolves_and_warns_once():
    target = _make_target_module("pyrit_tests_target_module_for_deprecation")
    try:
        getter = module_deprecation_getattr(
            old_module="legacy.module",
            target_module=target.__name__,
            names=["exposed_value", "another_value"],
            removed_in="9.9",
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            first = getter("exposed_value")
            second = getter("exposed_value")  # repeat: no new warning
            other = getter("another_value")  # different name: warns once

        assert first == 123
        assert second == 123
        assert other == "hello"
        assert len(w) == 2
        messages = [str(item.message) for item in w]
        assert any("legacy.module.exposed_value" in m for m in messages)
        assert any("legacy.module.another_value" in m for m in messages)
        for item in w:
            assert issubclass(item.category, DeprecationWarning)
            assert "9.9" in str(item.message)
            assert target.__name__ in str(item.message)
    finally:
        sys.modules.pop(target.__name__, None)


def test_module_deprecation_getattr_raises_for_unknown_name():
    target = _make_target_module("pyrit_tests_target_module_for_deprecation_unknown")
    try:
        getter = module_deprecation_getattr(
            old_module="legacy.module",
            target_module=target.__name__,
            names=["exposed_value"],
            removed_in="9.9",
        )
        try:
            getter("does_not_exist")
        except AttributeError as exc:
            assert "legacy.module" in str(exc)
            assert "does_not_exist" in str(exc)
        else:
            raise AssertionError("Expected AttributeError")
    finally:
        sys.modules.pop(target.__name__, None)


def test_module_deprecation_getattr_warnings_isolated_per_factory():
    """Each call to the factory has its own one-time-warning state."""
    target = _make_target_module("pyrit_tests_target_module_for_deprecation_isolated")
    try:
        getter_a = module_deprecation_getattr(
            old_module="legacy.module.a",
            target_module=target.__name__,
            names=["exposed_value"],
            removed_in="9.9",
        )
        getter_b = module_deprecation_getattr(
            old_module="legacy.module.b",
            target_module=target.__name__,
            names=["exposed_value"],
            removed_in="9.9",
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            getter_a("exposed_value")
            getter_a("exposed_value")  # no warning
            getter_b("exposed_value")  # warns once (separate factory)
            getter_b("exposed_value")  # no warning
        assert len(w) == 2
    finally:
        sys.modules.pop(target.__name__, None)
