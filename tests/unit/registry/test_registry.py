# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the standalone ``Registry`` base.

``ConverterRegistry`` overrides ``_get_registry_name`` and ``_identifier_type``, so
exercising the base only through it leaves the base's own defaults uncovered:
snake_case naming, the no-identifier path, eager vs. lazy discovery, the metadata
accessors, and the filter wiring. These tests drive a minimal subclass that keeps
every base default.
"""

from dataclasses import dataclass, field

import pytest

from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.registry import Registry, _get_metadata_value, _matches_filters


class SampleWidget:
    """A sample widget.

    A second paragraph that must not leak into the one-line summary.
    """

    def __init__(self, *, size: int = 1) -> None:
        self.size = size


class UndocumentedWidget:
    def __init__(self, *, size: int = 1) -> None:
        self.size = size


class UnregisteredWidget:
    """An unregistered widget."""

    def __init__(self, *, size: int = 1) -> None:
        self.size = size


class WidgetRegistry(Registry[object, ClassRegistryEntry]):
    """Minimal Registry subclass that keeps every base default."""

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        self.discover_calls = 0
        super().__init__(lazy_discovery=lazy_discovery)

    def _discover(self) -> None:
        self.discover_calls += 1
        self.register_class(SampleWidget)
        self.register_class(UndocumentedWidget)

    def _metadata_class(self) -> type[ClassRegistryEntry]:
        return ClassRegistryEntry


@dataclass(frozen=True)
class _TaggedMetadata(ClassRegistryEntry):
    tags: tuple[str, ...] = field(kw_only=True, default=())


def test_get_registry_name_defaults_to_snake_case():
    registry = WidgetRegistry()

    assert registry.get_class_names() == ["sample_widget", "undocumented_widget"]


def test_build_metadata_uses_first_paragraph_summary():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("sample_widget")

    assert meta is not None
    assert meta.class_description == "A sample widget."
    assert meta.class_name == "SampleWidget"
    assert meta.class_module == SampleWidget.__module__


def test_build_metadata_empty_description_without_docstring():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("undocumented_widget")

    assert meta is not None
    assert meta.class_description == ""


def test_class_attributes_empty_without_identifier_type():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("sample_widget")

    assert meta is not None
    assert meta.class_attributes == {}


def test_parameters_have_no_references_without_identifier_type():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("sample_widget")

    assert meta is not None
    assert all(p.reference is None for p in meta.parameters)


def test_create_instance_builds_object():
    registry = WidgetRegistry()

    widget = registry.create_instance("sample_widget", size=3)

    assert isinstance(widget, SampleWidget)
    assert widget.size == 3


def test_lazy_discovery_defers_until_access():
    registry = WidgetRegistry(lazy_discovery=True)

    assert registry.discover_calls == 0
    registry.get_class_names()
    assert registry.discover_calls == 1


def test_eager_discovery_runs_in_constructor():
    registry = WidgetRegistry(lazy_discovery=False)

    assert registry.discover_calls == 1


def test_get_registered_class_metadata_unknown_name_returns_none():
    registry = WidgetRegistry()

    assert registry.get_registered_class_metadata("does_not_exist") is None


def test_get_class_metadata_builds_for_unregistered_class():
    registry = WidgetRegistry()

    meta = registry.get_class_metadata(UnregisteredWidget)

    assert meta.class_name == "UnregisteredWidget"
    assert meta.registry_name == "unregistered_widget"
    assert "unregistered_widget" not in registry.get_class_names()


def test_get_class_unknown_name_raises():
    registry = WidgetRegistry()

    with pytest.raises(KeyError, match="not found in registry"):
        registry.get_class("nope")


def test_iter_and_contains_and_len():
    registry = WidgetRegistry()

    assert len(registry) == 2
    assert "sample_widget" in registry
    assert list(registry) == ["sample_widget", "undocumented_widget"]


def test_get_all_metadata_no_filters_returns_all():
    registry = WidgetRegistry()

    all_meta = registry.get_all_registered_class_metadata()

    assert {m.registry_name for m in all_meta} == {"sample_widget", "undocumented_widget"}


def test_get_all_metadata_include_filter_matches_subset():
    registry = WidgetRegistry()

    result = registry.get_all_registered_class_metadata(include_filters={"registry_name": "sample_widget"})

    assert [m.registry_name for m in result] == ["sample_widget"]


def test_get_all_metadata_exclude_filter_removes_match():
    registry = WidgetRegistry()

    result = registry.get_all_registered_class_metadata(exclude_filters={"registry_name": "sample_widget"})

    assert [m.registry_name for m in result] == ["undocumented_widget"]


def test_matches_filters_list_containment():
    meta = _TaggedMetadata(class_name="X", class_module="m", tags=("a", "b"))

    assert _matches_filters(meta, include_filters={"tags": "a"})
    assert not _matches_filters(meta, include_filters={"tags": "z"})
    assert not _matches_filters(meta, exclude_filters={"tags": "a"})


def test_matches_filters_unknown_include_key_fails():
    meta = ClassRegistryEntry(class_name="X", class_module="m")

    assert not _matches_filters(meta, include_filters={"nope": "x"})


def test_get_metadata_value_falls_back_to_params():
    class HasParams:
        def __init__(self) -> None:
            self.params = {"k": "v"}

    found, value = _get_metadata_value(HasParams(), "k")
    assert found is True
    assert value == "v"

    missing_found, missing_value = _get_metadata_value(HasParams(), "missing")
    assert missing_found is False
    assert missing_value is None
