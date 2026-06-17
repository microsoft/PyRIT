# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the instance-registry capability (``DefaultInstanceRegistry``) that a
registry exposes as its ``.instances`` property, plus the ``InstanceRegistry`` and
``SupportsInstances`` protocols.
"""

import pytest

from pyrit.models import ComponentIdentifier, Identifiable
from pyrit.registry.instance_registry import (
    DefaultInstanceRegistry,
    InstanceRegistry,
    RegistryEntry,
    SupportsInstances,
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

    def test_register_overwrites_existing(self, registry: DefaultInstanceRegistry[_TestItem]):
        registry.register(_item("original"), name="name")
        registry.register(_item("updated"), name="name")

        assert len(registry) == 1
        assert registry.get("name") == "updated"

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
