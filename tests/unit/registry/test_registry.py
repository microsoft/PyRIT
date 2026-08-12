# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the standalone ``Registry`` base.

``ConverterRegistry`` overrides ``_identifier_type`` and supplies discovery hooks, so
exercising the base only through it leaves the base's own defaults uncovered:
class-name keying, the no-identifier path, eager vs. lazy discovery, the metadata
accessors, and the filter wiring. These tests drive a minimal subclass that keeps
every base default.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from pyrit.registry.registry import Registry, _get_metadata_value, _matches_filters
from pyrit.registry.registry_metadata import RegistryMetadata


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


class PluginWidget:
    """A widget registered after discovery."""

    def __init__(self, *, size: int = 1) -> None:
        self.size = size


class WidgetRegistry(Registry[object, RegistryMetadata]):
    """Minimal Registry subclass that keeps every base default."""

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        self.discover_calls = 0
        super().__init__(lazy_discovery=lazy_discovery)

    def _discover(self) -> None:
        self.discover_calls += 1
        self.register_class(SampleWidget)
        self.register_class(UndocumentedWidget)

    def _metadata_class(self) -> type[RegistryMetadata]:
        return RegistryMetadata


class CoordinatedWidgetRegistry(WidgetRegistry):
    """Widget registry whose first metadata build can be held for concurrency tests."""

    def __init__(self) -> None:
        self.metadata_started = Event()
        self.metadata_release = Event()
        self.metadata_build_calls = 0
        super().__init__()

    def _build_metadata(self, name: str, cls: type[object]) -> RegistryMetadata:
        self.metadata_build_calls += 1
        if self.metadata_build_calls == 1:
            self.metadata_started.set()
            if not self.metadata_release.wait(timeout=5):
                raise TimeoutError("Metadata test release was not signaled.")
        return super()._build_metadata(name, cls)


class SlowConstructingRegistry(WidgetRegistry):
    """Registry subclass whose constructor pauses so singleton construction concurrency
    can be tested. Uses class-level events because ``get_registry_singleton`` calls
    ``cls()`` with no arguments."""

    construction_started = Event()
    construction_release = Event()

    def __init__(self) -> None:
        self.construction_started.set()
        if not self.construction_release.wait(timeout=5):
            raise TimeoutError("Slow-construction test release was not signaled.")
        super().__init__()


class OtherSlowConstructingRegistry(WidgetRegistry):
    """A distinct Registry subclass with an independent singleton key, used to prove
    that constructing SlowConstructingRegistry's singleton does not block this one."""


@dataclass(frozen=True)
class _TaggedMetadata(RegistryMetadata):
    tags: tuple[str, ...] = field(kw_only=True, default=())


def test_get_registry_name_defaults_to_class_name():
    registry = WidgetRegistry()

    assert registry.get_class_names() == ["SampleWidget", "UndocumentedWidget"]


def test_build_metadata_uses_first_paragraph_summary():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("SampleWidget")

    assert meta is not None
    assert meta.class_description == "A sample widget."
    assert meta.class_name == "SampleWidget"
    assert meta.class_module == SampleWidget.__module__


def test_build_metadata_empty_description_without_docstring():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("UndocumentedWidget")

    assert meta is not None
    assert meta.class_description == ""


def test_class_attributes_empty_without_identifier_type():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("SampleWidget")

    assert meta is not None
    assert meta.class_attributes == {}


def test_parameters_have_no_references_without_identifier_type():
    registry = WidgetRegistry()

    meta = registry.get_registered_class_metadata("SampleWidget")

    assert meta is not None
    assert all(p.reference is None for p in meta.parameters)


def test_create_instance_builds_object():
    registry = WidgetRegistry()

    widget = registry.create_instance("SampleWidget", size=3)

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
    assert meta.registry_name == "UnregisteredWidget"
    assert "UnregisteredWidget" not in registry.get_class_names()


def test_get_class_unknown_name_raises():
    registry = WidgetRegistry()

    with pytest.raises(KeyError, match="not found in registry"):
        registry.get_class("nope")


def test_iter_and_contains_and_len():
    registry = WidgetRegistry()

    assert len(registry) == 2
    assert "SampleWidget" in registry
    assert list(registry) == ["SampleWidget", "UndocumentedWidget"]


def test_get_all_metadata_no_filters_returns_all():
    registry = WidgetRegistry()

    all_meta = registry.get_all_registered_class_metadata()

    assert {m.registry_name for m in all_meta} == {"SampleWidget", "UndocumentedWidget"}


def test_concurrent_metadata_callers_share_one_cache_build() -> None:
    registry = CoordinatedWidgetRegistry()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(registry.get_all_registered_class_metadata)
        assert registry.metadata_started.wait(timeout=5)
        second = executor.submit(registry.get_all_registered_class_metadata)
        registry.metadata_release.set()

        assert first.result(timeout=5) == second.result(timeout=5)

    assert registry.metadata_build_calls == 2


def test_class_registration_does_not_block_behind_metadata_build_and_converges() -> None:
    """
    A concurrent metadata build must not park a fast catalog mutation like
    ``register_class`` behind its (potentially slow) work. The build must instead
    detect, via the catalog version stamp, that the catalog changed mid-build and
    retry so the returned metadata converges on a snapshot that includes the new
    class rather than caching a stale one.
    """
    registry = CoordinatedWidgetRegistry()

    with ThreadPoolExecutor(max_workers=2) as executor:
        initial_metadata = executor.submit(registry.get_all_registered_class_metadata)
        assert registry.metadata_started.wait(timeout=5)

        registration = executor.submit(registry.register_class, PluginWidget)
        # Registration only needs the (uncontended) catalog lock, so it must
        # complete promptly even though the metadata build is still paused.
        registration.result(timeout=1)

        registry.metadata_release.set()
        assert {item.class_name for item in initial_metadata.result(timeout=5)} == {
            "PluginWidget",
            "SampleWidget",
            "UndocumentedWidget",
        }

    refreshed = registry.get_all_registered_class_metadata()
    assert {item.class_name for item in refreshed} == {
        "PluginWidget",
        "SampleWidget",
        "UndocumentedWidget",
    }
    assert registry.metadata_build_calls == 5


def test_contains_and_get_class_do_not_block_behind_metadata_build() -> None:
    """
    ``__contains__`` and ``get_class`` must return promptly even while a metadata
    build is in flight and paused on another thread: they must never be parked
    behind the lock that guards the (slow) metadata build.
    """
    registry = CoordinatedWidgetRegistry()
    registry.get_class_names()  # Trigger discovery up front to isolate the build pause.

    with ThreadPoolExecutor(max_workers=3) as executor:
        metadata_future = executor.submit(registry.get_all_registered_class_metadata)
        assert registry.metadata_started.wait(timeout=5)

        contains_future = executor.submit(lambda: "SampleWidget" in registry)
        get_class_future = executor.submit(registry.get_class, "SampleWidget")

        assert contains_future.result(timeout=1) is True
        assert get_class_future.result(timeout=1) is SampleWidget

        registry.metadata_release.set()
        metadata_future.result(timeout=5)


def test_get_registry_singleton_converges_for_same_class() -> None:
    """Concurrent callers requesting the same registry class's singleton must
    converge onto a single construction."""
    SlowConstructingRegistry.construction_started.clear()
    SlowConstructingRegistry.construction_release.clear()
    SlowConstructingRegistry.reset_registry_singleton()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(SlowConstructingRegistry.get_registry_singleton)
            assert SlowConstructingRegistry.construction_started.wait(timeout=5)
            second = executor.submit(SlowConstructingRegistry.get_registry_singleton)
            SlowConstructingRegistry.construction_release.set()

            assert first.result(timeout=5) is second.result(timeout=5)
    finally:
        SlowConstructingRegistry.reset_registry_singleton()


def test_get_registry_singleton_does_not_block_unrelated_registry_class() -> None:
    """A slow, in-flight singleton construction for one registry class must not
    block singleton construction of an unrelated registry class."""
    SlowConstructingRegistry.construction_started.clear()
    SlowConstructingRegistry.construction_release.clear()
    SlowConstructingRegistry.reset_registry_singleton()
    OtherSlowConstructingRegistry.reset_registry_singleton()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            slow = executor.submit(SlowConstructingRegistry.get_registry_singleton)
            assert SlowConstructingRegistry.construction_started.wait(timeout=5)

            # Must complete promptly: different registry classes must not share a
            # construction lock.
            other = executor.submit(OtherSlowConstructingRegistry.get_registry_singleton)
            assert isinstance(other.result(timeout=1), OtherSlowConstructingRegistry)

            SlowConstructingRegistry.construction_release.set()
            assert isinstance(slow.result(timeout=5), SlowConstructingRegistry)
    finally:
        SlowConstructingRegistry.reset_registry_singleton()
        OtherSlowConstructingRegistry.reset_registry_singleton()


def test_get_all_metadata_include_filter_matches_subset():
    registry = WidgetRegistry()

    result = registry.get_all_registered_class_metadata(include_filters={"registry_name": "SampleWidget"})

    assert [m.registry_name for m in result] == ["SampleWidget"]


def test_get_all_metadata_exclude_filter_removes_match():
    registry = WidgetRegistry()

    result = registry.get_all_registered_class_metadata(exclude_filters={"registry_name": "SampleWidget"})

    assert [m.registry_name for m in result] == ["UndocumentedWidget"]


def test_matches_filters_list_containment():
    meta = _TaggedMetadata(class_name="X", class_module="m", tags=("a", "b"))

    assert _matches_filters(meta, include_filters={"tags": "a"})
    assert not _matches_filters(meta, include_filters={"tags": "z"})
    assert not _matches_filters(meta, exclude_filters={"tags": "a"})


def test_matches_filters_unknown_include_key_fails():
    meta = RegistryMetadata(class_name="X", class_module="m")

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


class _WidgetBase:
    """Base for the default-discovery hardening test."""


class _ConcreteWidget(_WidgetBase):
    """A concrete widget."""


class _PackageDrivenRegistry(Registry[object, RegistryMetadata]):
    """Registry that uses the base's default ``_discover`` over a supplied package."""

    def __init__(self, *, package: ModuleType) -> None:
        self._package = package
        super().__init__(lazy_discovery=False)

    def _base_type(self) -> type:
        return _WidgetBase

    def _discovery_package(self) -> ModuleType:
        return self._package

    def _metadata_class(self) -> type[RegistryMetadata]:
        return RegistryMetadata


def test_discover_skips_spec_type_mock_exports():
    # Type-based discovery enumerates concrete subclasses of the base that live under
    # the discovery package. A foreign export leaked into ``__all__`` (e.g. a
    # ``MagicMock(spec=type)``) is materialized by the lazy-export force-load but is
    # not a real subclass, so it must be ignored rather than blow up the catalog.
    package = ModuleType(_ConcreteWidget.__module__)
    package.__all__ = ["_ConcreteWidget", "_LeakedMock"]
    package._ConcreteWidget = _ConcreteWidget
    package._LeakedMock = MagicMock(spec=type)

    registry = _PackageDrivenRegistry(package=package)

    assert registry.get_class_names() == ["_ConcreteWidget"]
