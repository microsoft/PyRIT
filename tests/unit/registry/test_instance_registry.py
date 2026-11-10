# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the instance-registry capability (``DefaultInstanceRegistry``) that a
registry exposes as its ``.instances`` property, plus the ``InstanceRegistry`` and
``SupportsInstances`` protocols.
"""

import contextlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import assert_type
from unittest.mock import patch

import pytest

from pyrit.models import ComponentIdentifier, Identifiable
from pyrit.models.catalog import RegistryEntryOrigin, RestoreFailureReason, SavedEntryOwner
from pyrit.registry import (
    DefaultInstanceRegistry,
    InstanceRegistry,
    RegistryEntry,
    SavedEntryUnavailableError,
    SupportsInstances,
    TagQuery,
    UnavailableEntry,
)
from pyrit.registry.instance_registry import (
    CONFIGURATION_ENTRY_METADATA_KEY,
    SAVED_ENTRY_METADATA_KEY,
    SAVED_ENTRY_OWNER_METADATA_KEY,
    RegistryNameTakenError,
    SavedEntryReplacementError,
    current_registration_origin,
    record_saved_entry_refusals,
    registration_origin,
    registry_entry_origin,
    saved_entry_registration,
)


class _TestItem(Identifiable):
    """Minimal Identifiable stub wrapping a string value for testing."""

    def __init__(self, value: str) -> None:
        self.value = value

    def _build_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(
            class_name="_TestItem",
            class_module="test",
            params={"category": "test" if "test" in self.value.lower() else "other"},
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _TestItem):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"_TestItem({self.value!r})"


def _item(value: str) -> _TestItem:
    """Shorthand factory for _TestItem."""
    return _TestItem(value)


class _OtherItem(Identifiable):
    """A second Identifiable type, unrelated to _TestItem, for type-enforcement tests."""

    def _build_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(class_name="_OtherItem", class_module="test")


@pytest.fixture
def registry() -> DefaultInstanceRegistry[_TestItem]:
    """Provide a fresh, singleton-free instance registry for each test."""
    return DefaultInstanceRegistry()


class TestRegistration:
    """Tests for registering instances."""

    def test_register_adds_instance(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("test_value"), name="test_name")

        assert "test_name" in registry
        assert registry.get("test_name") == "test_value"

    def test_register_multiple_instances(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("value1"), name="name1")
        registry.register(_item("value2"), name="name2")
        registry.register(_item("value3"), name="name3")

        assert len(registry) == 3
        assert registry.get("name2") == "value2"

    def test_register_rejects_existing_name(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("original"), name="name")

        with pytest.raises(RegistryNameTakenError, match="already exists"):
            registry.register(_item("updated"), name="name")

        assert registry.get("name") == "original"

    def test_a_reserved_name_is_refused_but_not_as_taken(self):
        registry: DefaultInstanceRegistry[_TestItem] = DefaultInstanceRegistry(reserved_names={"types"})

        for check in (
            lambda: registry.register(_item("x"), name="types"),
            lambda: registry.validate_name_available("types"),
        ):
            with pytest.raises(ValueError, match="reserved") as refusal:
                check()
            assert not isinstance(refusal.value, RegistryNameTakenError)

    def test_register_can_explicitly_replace_existing(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("original"), name="name")
        registry.register(_item("updated"), name="name", replace=True)

        assert registry.get("name") == "updated"

    def test_register_refuses_to_replace_a_saved_entry(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("saved"), name="name", metadata={SAVED_ENTRY_METADATA_KEY: "targets"})
        revision = registry.revision

        with pytest.raises(SavedEntryReplacementError, match="Saved target 'name'"):
            registry.register(_item("updated"), name="name", replace=True)

        assert registry.get("name") == "saved"
        assert registry.revision == revision

    def test_refused_replacements_are_recorded_even_when_caught(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("saved"), name="name", metadata={SAVED_ENTRY_METADATA_KEY: "converters"})

        with record_saved_entry_refusals() as refusals:
            with contextlib.suppress(SavedEntryReplacementError):
                registry.register(_item("updated"), name="name", replace=True)
            registry.register(_item("other"), name="other", replace=True)

        assert refusals == [("converters", "name")]
        assert registry.get("other") == "other"

    def test_marks_entries_registered_while_the_startup_scripts_run(self, registry: DefaultInstanceRegistry[_TestItem]):
        startup_metadata = {"supports_tool_calls": True}
        with registration_origin(RegistryEntryOrigin.CONFIGURATION):
            registry.register(_item("startup"), name="startup", metadata=startup_metadata)
            registry.register(_item("replaced"), name="replaced")
        with registration_origin(RegistryEntryOrigin.TEMPORARY):
            registry.register(_item("launched"), name="launched")
        registry.register(_item("swapped in"), name="replaced", replace=True)
        registry.register(_item("created"), name="created")

        origins = {entry.name: registry_entry_origin(entry.metadata) for entry in registry.get_all_instances()}
        assert origins == {
            "created": RegistryEntryOrigin.TEMPORARY,
            "launched": RegistryEntryOrigin.TEMPORARY,
            "replaced": RegistryEntryOrigin.TEMPORARY,
            "startup": RegistryEntryOrigin.CONFIGURATION,
        }
        assert startup_metadata == {"supports_tool_calls": True}

    def test_the_registration_origin_ends_with_its_block_even_when_a_script_fails(self):
        with pytest.raises(RuntimeError, match="script failed"), registration_origin(RegistryEntryOrigin.CONFIGURATION):
            with registration_origin(RegistryEntryOrigin.TEMPORARY):
                assert current_registration_origin() is RegistryEntryOrigin.TEMPORARY
            assert current_registration_origin() is RegistryEntryOrigin.CONFIGURATION
            raise RuntimeError("script failed")

        assert current_registration_origin() is None

    @pytest.mark.parametrize(
        ("metadata", "origin"),
        [
            ({SAVED_ENTRY_METADATA_KEY: "targets"}, RegistryEntryOrigin.SAVED),
            ({SAVED_ENTRY_METADATA_KEY: "targets", CONFIGURATION_ENTRY_METADATA_KEY: True}, RegistryEntryOrigin.SAVED),
            ({CONFIGURATION_ENTRY_METADATA_KEY: True}, RegistryEntryOrigin.CONFIGURATION),
            ({}, RegistryEntryOrigin.TEMPORARY),
            ({"supports_tool_calls": True}, RegistryEntryOrigin.TEMPORARY),
        ],
    )
    def test_tells_where_an_entry_comes_from(self, metadata: dict[str, object], origin: RegistryEntryOrigin):
        assert registry_entry_origin(metadata) is origin

    def test_a_restore_scope_marks_only_the_entry_it_names(self, registry: DefaultInstanceRegistry[_TestItem]):
        ada = SavedEntryOwner(oid="00000000-0000-0000-0000-00000000000a", name="Ada")
        with saved_entry_registration(kind="targets", name="saved", owner=ada):
            registry.register(_item("saved"), name="saved", metadata={"note": "kept"})
            registry.register(_item("helper"), name="helper")
        registry.register(_item("later"), name="later")

        assert registry.get_entry("saved").metadata == {
            "note": "kept",
            SAVED_ENTRY_METADATA_KEY: "targets",
            SAVED_ENTRY_OWNER_METADATA_KEY: ada,
        }
        assert registry.get_entry("helper").metadata == {}
        assert registry.get_entry("later").metadata == {}

    def test_a_caught_duplicate_name_for_a_saved_entry_is_recorded(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("saved"), name="name", metadata={SAVED_ENTRY_METADATA_KEY: "targets"})
        registry.register(_item("startup"), name="startup")

        with record_saved_entry_refusals() as refusals:
            for name in ("name", "startup"):
                with pytest.raises(ValueError, match="already exists"):
                    registry.register(_item("updated"), name=name)

        assert refusals == [("targets", "name")]
        assert registry.get("name") == "saved"

    def test_a_caught_name_check_against_a_saved_entry_is_recorded(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("saved"), name="name", metadata={SAVED_ENTRY_METADATA_KEY: "converters"})
        registry.register(_item("startup"), name="startup")

        with record_saved_entry_refusals() as refusals:
            for name in ("name", "startup"):
                with pytest.raises(ValueError, match="already exists"):
                    registry.validate_name_available(name)
            registry.validate_name_available("free")

        assert refusals == [("converters", "name")]

    def test_register_rejects_reserved_name(self):
        registry: DefaultInstanceRegistry[_TestItem] = DefaultInstanceRegistry(reserved_names={"types"})

        with pytest.raises(ValueError, match="reserved"):
            registry.register(_item("value"), name="types")

    def test_register_defaults_name_to_identifier_unique_name(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("value1"))

        names = registry.get_names()
        assert len(names) == 1
        assert names[0].startswith("_TestItem::")

    def test_register_invalidates_metadata_cache(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("value1"), name="name1")
        assert len(registry.list_metadata()) == 1

        registry.register(_item("value2"), name="name2")
        assert len(registry.list_metadata()) == 2


def test_concurrent_registration_preserves_one_winner(registry: DefaultInstanceRegistry[_TestItem]) -> None:
    ready = Barrier(2, timeout=5)
    normalize_tags = registry._normalize_tags

    def prepare_registration(tags: dict[str, str] | list[str] | None = None) -> dict[str, str]:
        ready.wait()
        return normalize_tags(tags)

    with (
        patch.object(registry, "_normalize_tags", side_effect=prepare_registration),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        items = [_item("first"), _item("second")]
        futures = [executor.submit(registry.register, item, name="same") for item in items]
        successes = []
        failures = []
        for item, future in zip(items, futures, strict=True):
            try:
                future.result(timeout=5)
            except ValueError as exc:
                failures.append(exc)
            else:
                successes.append(item)

    assert len(successes) == len(failures) == 1
    assert "already exists" in str(failures[0])
    assert registry.get("same") is successes[0]


def test_concurrent_unregister_removes_instance_once(registry: DefaultInstanceRegistry[_TestItem]) -> None:
    item = _item("first")
    registry.register(item, name="same")
    ready = Barrier(2, timeout=5)

    def remove() -> _TestItem | None:
        ready.wait()
        return registry.unregister("same")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(remove) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]

    assert sum(result is item for result in results) == 1
    assert sum(result is None for result in results) == 1
    assert registry.get_all_instances() == []


def test_metadata_retries_after_concurrent_registration(registry: DefaultInstanceRegistry[_TestItem]) -> None:
    registry.register(_item("first"), name="first")
    started = Event()
    release = Event()
    build_metadata = registry._build_metadata

    def blocked_metadata(item: _TestItem) -> ComponentIdentifier:
        if not started.is_set():
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Metadata build was not released")
        return build_metadata(item)

    with (
        patch.object(registry, "_build_metadata", side_effect=blocked_metadata),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(registry.list_metadata)
        try:
            assert started.wait(timeout=5)
            registry.register(_item("second"), name="second")
        finally:
            release.set()
        metadata = future.result(timeout=5)

    assert len(metadata) == 2
    assert registry.list_metadata() is metadata


class TestInstanceTypeEnforcement:
    """Tests for the optional ``instance_type`` registration constraint."""

    def test_register_accepts_matching_type(self):
        registry: DefaultInstanceRegistry[_TestItem] = DefaultInstanceRegistry(instance_type=_TestItem)
        registry.register(_item("value1"), name="name1")
        assert registry.get("name1") == "value1"

    def test_register_rejects_mismatched_type(self):
        registry: DefaultInstanceRegistry[_TestItem] = DefaultInstanceRegistry(instance_type=_TestItem)
        with pytest.raises(TypeError, match="_OtherItem.*_TestItem"):
            registry.register(_OtherItem(), name="wrong")  # type: ignore[arg-type]
        assert "wrong" not in registry

    def test_register_accepts_subclass_of_expected_type(self):
        class _SubItem(_TestItem):
            pass

        registry: DefaultInstanceRegistry[_TestItem] = DefaultInstanceRegistry(instance_type=_TestItem)
        registry.register(_SubItem("value1"), name="name1")
        assert registry.get("name1") == "value1"

    def test_instance_type_accepts_lazy_callable(self):
        calls = 0

        def provide_type() -> type[_TestItem]:
            nonlocal calls
            calls += 1
            return _TestItem

        registry: DefaultInstanceRegistry[_TestItem] = DefaultInstanceRegistry(instance_type=provide_type)
        registry.register(_item("value1"), name="name1")
        registry.register(_item("value2"), name="name2")

        assert calls == 1  # resolved once and cached
        with pytest.raises(TypeError):
            registry.register(_OtherItem(), name="wrong")  # type: ignore[arg-type]

    def test_no_instance_type_allows_any_identifiable(self):
        registry: DefaultInstanceRegistry[Identifiable] = DefaultInstanceRegistry()
        registry.register(_item("value1"), name="a")
        registry.register(_OtherItem(), name="b")
        assert registry.get_names() == ["a", "b"]


class TestGet:
    """Tests for get and get_entry."""

    def test_get_existing_instance(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("value1"), name="name1")
        assert registry.get("name1") == "value1"

    def test_get_nonexistent_returns_none(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry.get("missing") is None

    def test_get_entry_returns_registry_entry(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("value1"), name="name1", tags=["fast"])
        entry = registry.get_entry("name1")
        assert isinstance(entry, RegistryEntry)
        assert entry is not None
        assert entry.name == "name1"
        assert entry.instance == "value1"
        assert entry.tags == {"fast": ""}

    def test_get_entry_nonexistent_returns_none(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry.get_entry("missing") is None

    def test_get_and_get_entry_preserve_return_types(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        item = _item("value1")
        registry.register(item, name="name1")

        retrieved = registry.get("name1")
        assert_type(retrieved, _TestItem | None)
        assert retrieved is item

        missing = registry.get("missing")
        assert_type(missing, _TestItem | None)
        assert missing is None

        entry = registry.get_entry("name1")
        assert_type(entry, RegistryEntry[_TestItem] | None)
        assert entry is not None
        assert entry.instance is item

        missing_entry = registry.get_entry("missing")
        assert_type(missing_entry, RegistryEntry[_TestItem] | None)
        assert missing_entry is None


class TestUnregister:
    """Tests for unregistering instances."""

    def test_unregister_removes_and_returns_instance(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        item = _item("value1")
        registry.register(item, name="name1")

        assert registry.unregister("name1") is item
        assert registry.get("name1") is None

    def test_unregister_missing_returns_none(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        assert registry.unregister("missing") is None

    def test_unregister_expected_entry_preserves_replacement(
        self, registry: DefaultInstanceRegistry[_TestItem]
    ) -> None:
        item = _item("item")
        registry.register(item, name="name", metadata={"version": 1})
        original_entry = registry.get_entry("name")
        assert original_entry is not None
        registry.register(item, name="name", metadata={"version": 2}, replace=True)

        assert registry.unregister("name", expected_entry=original_entry) is None
        assert registry.get("name") is item
        replacement_entry = registry.get_entry("name")
        assert replacement_entry is not None
        assert replacement_entry.metadata == {"version": 2}

    def test_unregister_invalidates_metadata_cache(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        registry.register(_item("value1"), name="name1")
        assert len(registry.list_metadata()) == 1

        registry.unregister("name1")

        assert registry.list_metadata() == []


class TestGetNamesAndAllInstances:
    """Tests for get_names and get_all_instances."""

    def test_get_names_empty_registry(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry.get_names() == []

    def test_get_names_returns_sorted_list(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="zeta")
        registry.register(_item("v2"), name="alpha")
        assert registry.get_names() == ["alpha", "zeta"]

    def test_get_all_instances_sorted_by_name(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="zeta")
        registry.register(_item("v2"), name="alpha")
        assert [e.name for e in registry.get_all_instances()] == ["alpha", "zeta"]

    def test_get_all_instances_preserves_tags(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags={"speed": "fast"})
        entry = registry.get_all_instances()[0]
        assert entry.tags == {"speed": "fast"}

    def test_get_all_instances_empty_registry(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry.get_all_instances() == []

    def test_get_all_instances_preserves_return_type(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        registry.register(_item("v1"), name="zeta")
        registry.register(_item("v2"), name="alpha")

        all_instances = registry.get_all_instances()
        assert_type(all_instances, list[RegistryEntry[_TestItem]])
        assert [e.name for e in all_instances] == ["alpha", "zeta"]


class TestListMetadata:
    """Tests for list_metadata and its filtering/caching."""

    def test_list_metadata_returns_all_items(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("test_v1"), name="n1")
        registry.register(_item("other_v2"), name="n2")
        metadata = registry.list_metadata()
        assert len(metadata) == 2
        assert all(isinstance(m, ComponentIdentifier) for m in metadata)

    def test_list_metadata_with_include_filter(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("test_v1"), name="n1")
        registry.register(_item("other_v2"), name="n2")
        metadata = registry.list_metadata(include_filters={"category": "test"})
        assert len(metadata) == 1
        assert metadata[0].params["category"] == "test"

    def test_list_metadata_with_exclude_filter(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("test_v1"), name="n1")
        registry.register(_item("other_v2"), name="n2")
        metadata = registry.list_metadata(exclude_filters={"category": "test"})
        assert len(metadata) == 1
        assert metadata[0].params["category"] == "other"

    def test_list_metadata_caches_until_invalidated(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("test_v1"), name="n1")
        first = registry.list_metadata()
        second = registry.list_metadata()
        assert first is second


class TestTags:
    """Tests for tag storage, normalization, and queries."""

    def test_register_with_dict_tags(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags={"speed": "fast"})
        assert registry.get_entry("n1").tags == {"speed": "fast"}

    def test_register_with_list_tags_defaults_empty_values(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["fast", "stable"])
        assert registry.get_entry("n1").tags == {"fast": "", "stable": ""}

    def test_get_by_tag_key_only(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["fast"])
        registry.register(_item("v2"), name="n2", tags=["slow"])
        results = registry.get_by_tag(tag="fast")
        assert [e.name for e in results] == ["n1"]

    def test_get_by_tag_key_and_value(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags={"speed": "fast"})
        registry.register(_item("v2"), name="n2", tags={"speed": "slow"})
        results = registry.get_by_tag(tag="speed", value="fast")
        assert [e.name for e in results] == ["n1"]

    def test_get_by_tag_returns_sorted_by_name(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="zeta", tags=["t"])
        registry.register(_item("v2"), name="alpha", tags=["t"])
        assert [e.name for e in registry.get_by_tag(tag="t")] == ["alpha", "zeta"]

    def test_get_by_tag_no_match(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["fast"])
        assert registry.get_by_tag(tag="missing") == []

    def test_query_by_tags_and_predicate(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["core", "fast"])
        registry.register(_item("v2"), name="n2", tags=["core", "slow"])
        registry.register(_item("v3"), name="n3", tags=["fast"])
        query = TagQuery.all("core") & TagQuery.any_of("fast", "cheap")
        assert [e.name for e in registry.query_by_tags(query=query)] == ["n1"]

    def test_query_by_tags_exclude(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["core"])
        registry.register(_item("v2"), name="n2", tags=["core", "deprecated"])
        query = TagQuery.all("core") & TagQuery.none_of("deprecated")
        assert [e.name for e in registry.query_by_tags(query=query)] == ["n1"]

    def test_query_by_tags_matches_keys_ignoring_values(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags={"speed": "fast"})
        registry.register(_item("v2"), name="n2", tags={"speed": "slow"})
        assert [e.name for e in registry.query_by_tags(query=TagQuery.all("speed"))] == ["n1", "n2"]

    def test_query_by_tags_returns_sorted_by_name(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="zeta", tags=["t"])
        registry.register(_item("v2"), name="alpha", tags=["t"])
        assert [e.name for e in registry.query_by_tags(query=TagQuery.any_of("t"))] == ["alpha", "zeta"]

    def test_query_by_tags_empty_query_returns_all(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["a"])
        registry.register(_item("v2"), name="n2", tags=["b"])
        assert [e.name for e in registry.query_by_tags(query=TagQuery())] == ["n1", "n2"]

    def test_normalize_tags_none(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry._normalize_tags(None) == {}

    def test_normalize_tags_list(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry._normalize_tags(["a", "b"]) == {"a": "", "b": ""}

    def test_normalize_tags_dict(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert registry._normalize_tags({"a": "1"}) == {"a": "1"}


class TestAddTags:
    """Tests for add_tags."""

    def test_add_tags_with_list(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1")
        registry.add_tags(name="n1", tags=["fast"])
        assert registry.get_entry("n1").tags == {"fast": ""}

    def test_add_tags_merges_with_existing(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags={"a": "1"})
        registry.add_tags(name="n1", tags={"b": "2"})
        assert registry.get_entry("n1").tags == {"a": "1", "b": "2"}

    def test_add_tags_raises_for_missing_entry(self, registry: DefaultInstanceRegistry[_TestItem]):
        with pytest.raises(KeyError):
            registry.add_tags(name="missing", tags=["fast"])

    def test_add_tags_invalidates_metadata_cache(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1")
        first = registry.list_metadata()
        registry.add_tags(name="n1", tags=["fast"])
        second = registry.list_metadata()
        assert first is not second

    def test_add_tags_entries_findable_by_get_by_tag(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1")
        registry.add_tags(name="n1", tags={"speed": "fast"})
        assert [e.name for e in registry.get_by_tag(tag="speed", value="fast")] == ["n1"]


class TestDunderMethods:
    """Tests for __contains__, __len__, and __iter__."""

    def test_contains(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1")
        assert "n1" in registry
        assert "missing" not in registry

    def test_len(self, registry: DefaultInstanceRegistry[_TestItem]):
        assert len(registry) == 0
        registry.register(_item("v1"), name="n1")
        assert len(registry) == 1

    def test_iter_returns_sorted_names(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="zeta")
        registry.register(_item("v2"), name="alpha")
        assert list(registry) == ["alpha", "zeta"]


class TestMetadataField:
    """Tests for the metadata field on RegistryEntry."""

    def test_register_with_metadata_stores_it(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", metadata={"accepts_scorer_override": False, "priority": 5})
        entry = registry.get_entry("n1")
        assert entry.metadata == {"accepts_scorer_override": False, "priority": 5}

    def test_register_without_metadata_defaults_to_empty_dict(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1")
        assert registry.get_entry("n1").metadata == {}

    def test_metadata_does_not_affect_tags(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("v1"), name="n1", tags=["fast"], metadata={"key": "value"})
        entry = registry.get_entry("n1")
        assert entry.tags == {"fast": ""}
        assert entry.metadata == {"key": "value"}
        assert registry.get_by_tag(tag="key") == []


class _IdentifiableStub(Identifiable):
    """A minimal stub that holds a ComponentIdentifier for dependency tests."""

    def __init__(self, identifier: ComponentIdentifier) -> None:
        self._stored_identifier = identifier

    def _build_identifier(self) -> ComponentIdentifier:
        return self._stored_identifier


class TestFindDependentsOfTag:
    """Tests for DefaultInstanceRegistry.find_dependents_of_tag."""

    @pytest.fixture
    def registry(self) -> DefaultInstanceRegistry[_IdentifiableStub]:
        return DefaultInstanceRegistry()

    def test_no_tagged_entries_returns_empty(self, registry: DefaultInstanceRegistry[_IdentifiableStub]) -> None:
        registry.register(_IdentifiableStub(ComponentIdentifier(class_name="A", class_module="mod")), name="a")
        assert registry.find_dependents_of_tag(tag="refusal") == []

    def test_tagged_entry_not_returned_as_dependent(self, registry: DefaultInstanceRegistry[_IdentifiableStub]) -> None:
        stub = _IdentifiableStub(ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="r1"))
        registry.register(stub, name="refusal_scorer", tags=["refusal"])
        assert registry.find_dependents_of_tag(tag="refusal") == []

    def test_dependent_found_by_child_eval_hash(self, registry: DefaultInstanceRegistry[_IdentifiableStub]) -> None:
        base_id = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="r_hash")
        registry.register(_IdentifiableStub(base_id), name="refusal_scorer", tags=["refusal"])

        child_id = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="r_hash")
        wrapper_id = ComponentIdentifier(
            class_name="Inverter",
            class_module="mod",
            eval_hash="w_hash",
            children={"sub_scorers": [child_id]},
        )
        registry.register(_IdentifiableStub(wrapper_id), name="inverter")

        dependents = registry.find_dependents_of_tag(tag="refusal")
        assert [d.name for d in dependents] == ["inverter"]

    def test_non_dependent_not_returned(self, registry: DefaultInstanceRegistry[_IdentifiableStub]) -> None:
        base_id = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="r_hash")
        registry.register(_IdentifiableStub(base_id), name="refusal_scorer", tags=["refusal"])

        unrelated_id = ComponentIdentifier(class_name="Likert", class_module="mod", eval_hash="l_hash")
        registry.register(_IdentifiableStub(unrelated_id), name="likert")

        assert registry.find_dependents_of_tag(tag="refusal") == []

    def test_deeply_nested_dependency_found(self, registry: DefaultInstanceRegistry[_IdentifiableStub]) -> None:
        base_id = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="deep_r")
        registry.register(_IdentifiableStub(base_id), name="refusal_scorer", tags=["refusal"])

        inner_child = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="deep_r")
        inverter = ComponentIdentifier(
            class_name="Inverter",
            class_module="mod",
            children={"sub_scorers": [inner_child]},
        )
        composite_id = ComponentIdentifier(
            class_name="Composite",
            class_module="mod",
            children={"sub_scorers": [inverter]},
        )
        registry.register(_IdentifiableStub(composite_id), name="composite")

        dependents = registry.find_dependents_of_tag(tag="refusal")
        assert [d.name for d in dependents] == ["composite"]

    def test_multiple_dependents_returned_sorted(self, registry: DefaultInstanceRegistry[_IdentifiableStub]) -> None:
        base_id = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="r1")
        registry.register(_IdentifiableStub(base_id), name="refusal_scorer", tags=["refusal"])

        child = ComponentIdentifier(class_name="Refusal", class_module="mod", eval_hash="r1")
        for wrapper_name in ["z_wrapper", "a_wrapper", "m_wrapper"]:
            wrapper_id = ComponentIdentifier(
                class_name="Wrapper",
                class_module="mod",
                children={"sub_scorers": [child]},
            )
            registry.register(_IdentifiableStub(wrapper_id), name=wrapper_name)

        dependents = registry.find_dependents_of_tag(tag="refusal")
        assert [d.name for d in dependents] == ["a_wrapper", "m_wrapper", "z_wrapper"]


def _held(name: str | None, *, message: str = "ValueError: boom") -> UnavailableEntry:
    return UnavailableEntry(kind="target", name=name, reason=RestoreFailureReason.BUILD_ERROR, message=message)


class TestUnavailableEntries:
    """Tests for names held by saved entries that couldn't be restored."""

    def test_a_held_name_fails_lookups_with_the_reason(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        registry.mark_unavailable(_held("broken"))

        with pytest.raises(SavedEntryUnavailableError, match="'broken' couldn't be restored: ValueError: boom"):
            registry.get("broken")
        with pytest.raises(SavedEntryUnavailableError):
            registry.get_entry("broken")
        assert "broken" in registry
        assert registry.get("missing") is None

    def test_a_held_name_hides_an_instance_registered_under_it(
        self, registry: DefaultInstanceRegistry[_TestItem]
    ) -> None:
        registry.register(_item("startup"), name="shared")
        registry.register(_item("other"), name="other")
        revision = registry.revision

        registry.mark_unavailable(_held("shared"))

        assert registry.revision > revision
        assert registry.get_names() == ["other"]
        assert [entry.name for entry in registry.get_all_instances()] == ["other"]
        assert len(registry) == 1
        with pytest.raises(SavedEntryUnavailableError):
            registry.get("shared")

    @pytest.mark.parametrize("replace", [False, True])
    def test_a_held_name_cant_be_taken(self, registry: DefaultInstanceRegistry[_TestItem], replace: bool) -> None:
        registry.mark_unavailable(_held("broken"))

        with pytest.raises(SavedEntryUnavailableError):
            registry.validate_name_available("broken")
        with pytest.raises(SavedEntryUnavailableError):
            registry.register(_item("stand_in"), name="broken", replace=replace)

    def test_clearing_a_hold_releases_the_name(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        registry.mark_unavailable(_held("broken"))

        registry.clear_unavailable("broken")
        registry.register(_item("fixed"), name="broken")

        assert registry.get("broken") == "fixed"
        assert registry.get_unavailable_entries() == []

    def test_a_held_folder_holds_every_unregistered_name(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        registry.register(_item("startup"), name="startup")
        folder = _held(None, message="The saved targets couldn't be listed: Permission denied")

        registry.mark_unavailable(folder)

        assert registry.get("startup") == "startup"
        with pytest.raises(SavedEntryUnavailableError, match="'maybe_saved' may be a saved target"):
            registry.get("maybe_saved")
        with pytest.raises(SavedEntryUnavailableError):
            registry.register(_item("new"), name="maybe_saved")
        assert registry.get_unavailable_entries() == [folder]

    def test_the_description_hides_the_message_when_asked(self) -> None:
        entry = _held("broken", message="ValueError: https://host/v1")

        error = SavedEntryUnavailableError(entry=entry, name="broken")

        assert (
            error.describe(with_details=False)
            == "The saved target 'broken' couldn't be restored. It couldn't be built."
        )
        assert "https://host/v1" in str(error)


class TestProtocolConformance:
    """Tests that DefaultInstanceRegistry satisfies the registry protocols."""

    def test_default_impl_is_instance_registry(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        assert isinstance(registry, InstanceRegistry)

    def test_supports_instances_marker(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        class _Holder:
            def __init__(self, instances: InstanceRegistry[_TestItem]) -> None:
                self.instances = instances

        holder: SupportsInstances[_TestItem] = _Holder(registry)
        holder.instances.register(_item("v1"), name="n1")
        assert holder.instances.get("n1") == "v1"

    def test_instance_registry_consumer_preserves_types(self, registry: DefaultInstanceRegistry[_TestItem]) -> None:
        def check_consumer(consumer: InstanceRegistry[_TestItem]) -> None:
            item = _item("v1")
            consumer.register(item, name="n1")

            retrieved = consumer.get("n1")
            assert_type(retrieved, _TestItem | None)
            assert retrieved is item

            missing = consumer.get("missing")
            assert_type(missing, _TestItem | None)
            assert missing is None

            entry = consumer.get_entry("n1")
            assert_type(entry, RegistryEntry[_TestItem] | None)
            assert entry is not None
            assert entry.instance is item

            missing_entry = consumer.get_entry("missing")
            assert_type(missing_entry, RegistryEntry[_TestItem] | None)
            assert missing_entry is None

            all_instances = consumer.get_all_instances()
            assert_type(all_instances, list[RegistryEntry[_TestItem]])
            assert [e.name for e in all_instances] == ["n1"]

        check_consumer(registry)


class TestNoBackendDependency:
    """The instance registry must be reusable without depending on pyrit.backend."""

    def test_module_has_no_backend_dependency(self) -> None:
        import ast
        import inspect

        import pyrit.registry.instance_registry as module

        tree = ast.parse(inspect.getsource(module))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        assert not any(name.startswith("pyrit.backend") for name in imported_modules)
