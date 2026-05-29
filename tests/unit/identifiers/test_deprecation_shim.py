# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the ``pyrit.identifiers`` deprecation shim.

The shim was installed when ``pyrit.identifiers`` was renamed to
``pyrit.models.identifiers`` (Phase 2 of the models refactor). These tests
ensure the shim correctly forwards every public symbol to the new location,
emits a ``DeprecationWarning`` exactly once per name per process, and raises
``AttributeError`` for unknown attributes — matching the behavior contract
documented in ``pyrit/identifiers/__init__.py``.
"""

from __future__ import annotations

import importlib
import warnings

import pytest

import pyrit.identifiers as shim
import pyrit.identifiers.atomic_attack_identifier as shim_atomic
import pyrit.identifiers.class_name_utils as shim_class_name
import pyrit.identifiers.component_identifier as shim_component
import pyrit.identifiers.evaluation_identifier as shim_eval
import pyrit.identifiers.identifier_filters as shim_filters
import pyrit.models.identifiers as new
import pyrit.models.identifiers.atomic_attack_identifier as new_atomic
import pyrit.models.identifiers.class_name_utils as new_class_name
import pyrit.models.identifiers.component_identifier as new_component
import pyrit.models.identifiers.evaluation_identifier as new_eval
import pyrit.models.identifiers.identifier_filters as new_filters

SUBMODULE_PAIRS = [
    (shim_component, new_component, "component_identifier"),
    (shim_atomic, new_atomic, "atomic_attack_identifier"),
    (shim_eval, new_eval, "evaluation_identifier"),
    (shim_class_name, new_class_name, "class_name_utils"),
    (shim_filters, new_filters, "identifier_filters"),
]


@pytest.fixture(autouse=True)
def _reset_warning_caches():
    """Reset every shim's per-process `_warned` set so each test starts clean."""
    saved = {}
    modules = [shim] + [m for m, _, _ in SUBMODULE_PAIRS]
    for mod in modules:
        saved[mod] = set(mod._warned)
        mod._warned.clear()
    try:
        yield
    finally:
        for mod, original in saved.items():
            mod._warned.clear()
            mod._warned.update(original)


@pytest.mark.parametrize("name", shim.__all__)
def test_top_level_shim_forwards_to_new_module(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim_obj = getattr(shim, name)
        new_obj = getattr(new, name)
    assert shim_obj is new_obj


@pytest.mark.parametrize("name", shim.__all__)
def test_top_level_shim_emits_one_warning_per_name(name):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        getattr(shim, name)
        getattr(shim, name)
        getattr(shim, name)

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, f"Expected 1 DeprecationWarning for {name!r}, got {len(dep)}"
    message = str(dep[0].message)
    assert f"pyrit.identifiers.{name}" in message
    assert f"pyrit.models.identifiers.{name}" in message
    assert "0.16.0" in message


def test_top_level_shim_attribute_error_for_unknown_name():
    with pytest.raises(AttributeError, match="has no attribute 'definitely_not_a_real_name'"):
        _ = shim.definitely_not_a_real_name


def test_top_level_shim_dir_returns_all_public_names():
    assert dir(shim) == sorted(shim.__all__)


@pytest.mark.parametrize("shim_mod, new_mod, label", SUBMODULE_PAIRS)
def test_submodule_shim_forwards_every_name(shim_mod, new_mod, label):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in shim_mod.__all__:
            assert getattr(shim_mod, name) is getattr(new_mod, name), (
                f"{label}.{name} did not forward to new module"
            )


@pytest.mark.parametrize("shim_mod, _new_mod, label", SUBMODULE_PAIRS)
def test_submodule_shim_warns_once_per_name(shim_mod, _new_mod, label):
    for name in shim_mod.__all__:
        shim_mod._warned.clear()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            getattr(shim_mod, name)
            getattr(shim_mod, name)

        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) == 1, (
            f"Expected 1 DeprecationWarning for {label}.{name}, got {len(dep)}"
        )
        message = str(dep[0].message)
        assert f"pyrit.identifiers.{label}.{name}" in message
        assert f"pyrit.models.identifiers.{label}.{name}" in message
        assert "0.16.0" in message


@pytest.mark.parametrize("shim_mod, _new_mod, label", SUBMODULE_PAIRS)
def test_submodule_shim_attribute_error_for_unknown_name(shim_mod, _new_mod, label):
    with pytest.raises(AttributeError, match=f"'pyrit.identifiers.{label}'"):
        _ = shim_mod.definitely_not_a_real_name


def test_submodule_shim_from_import_style_returns_new_class():
    """`from pyrit.identifiers.component_identifier import ComponentIdentifier` works."""
    # Force re-import via importlib to confirm the from-import codepath fires __getattr__.
    importlib.reload(shim_component)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from pyrit.identifiers.component_identifier import ComponentIdentifier as ShimCI

    from pyrit.models.identifiers.component_identifier import ComponentIdentifier as NewCI

    assert ShimCI is NewCI


def test_submodule_shim_attribute_access_style_returns_new_class():
    """`import pyrit.identifiers.X; X.ComponentIdentifier` works."""
    import pyrit.identifiers.component_identifier as mod

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        cls = mod.ComponentIdentifier

    from pyrit.models.identifiers.component_identifier import ComponentIdentifier as NewCI

    assert cls is NewCI


def test_warning_stacklevel_attributes_to_caller():
    """`stacklevel=3` should attribute the warning to the test file, not the shim."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        getattr(shim, "ComponentIdentifier")  # noqa: B009  (intentional attribute access)

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1
    assert dep[0].filename.endswith("test_deprecation_shim.py"), (
        f"Expected warning attributed to this test file, got {dep[0].filename}"
    )


def test_top_level_shim_does_not_warn_on_internal_attribute_access():
    """Accessing module-level internals (e.g., the helper alias `_new`) must NOT warn."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        _ = shim._new
        _ = shim.__all__
        _ = shim._warned

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert dep == [], f"Internal-attribute access should not warn, got: {[str(w.message) for w in dep]}"
