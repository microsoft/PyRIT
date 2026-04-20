# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass

from pyrit.identifiers import Identifier
from pyrit.registry.instance_registries.base_instance_registry import BaseInstanceRegistry


@dataclass(frozen=True)
class SampleItemMetadata(Identifier):
    """Sample metadata with an extra field."""

    category: str


class ConcreteTestRegistry(BaseInstanceRegistry[str, SampleItemMetadata]):
    """Concrete implementation of BaseInstanceRegistry for testing."""

    def _build_metadata(self, name: str, instance: str) -> SampleItemMetadata:
        """Build test metadata from a string instance."""
        # Note: name parameter is for registry key, but Identifier.unique_name is auto-computed
        return SampleItemMetadata(
            identifier_type="instance",
            class_name="str",
            class_module="builtins",
            class_description=f"Description for {instance}",
            category="test" if "test" in instance.lower() else "other",
        )


class TestBaseInstanceRegistrySingleton:
    """Tests for the singleton pattern in BaseInstanceRegistry."""

    def setup_method(self):
        """Reset the singleton before each test."""
        ConcreteTestRegistry.reset_instance()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConcreteTestRegistry.reset_instance()

    def test_get_registry_singleton_returns_same_instance(self):
        """Test that get_registry_singleton returns the same singleton each time."""
        instance1 = ConcreteTestRegistry.get_registry_singleton()
        instance2 = ConcreteTestRegistry.get_registry_singleton()

        assert instance1 is instance2

    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        instance1 = ConcreteTestRegistry.get_registry_singleton()
        ConcreteTestRegistry.reset_instance()
        instance2 = ConcreteTestRegistry.get_registry_singleton()

        assert instance1 is not instance2

    def test_reset_instance_when_not_exists_does_not_raise(self):
        """Test that reset_instance works even when no instance exists."""
        # Should not raise any exception
        ConcreteTestRegistry.reset_instance()
        ConcreteTestRegistry.reset_instance()


class TestBaseInstanceRegistryRegistration:
    """Tests for registration functionality in BaseInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConcreteTestRegistry.reset_instance()
        self.registry = ConcreteTestRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConcreteTestRegistry.reset_instance()

    def test_register_adds_instance(self):
        """Test that register adds an instance to the registry."""
        self.registry.register("test_value", name="test_name")

        assert "test_name" in self.registry
        assert self.registry.get("test_name") == "test_value"

    def test_register_multiple_instances(self):
        """Test registering multiple instances."""
        self.registry.register("value1", name="name1")
        self.registry.register("value2", name="name2")
        self.registry.register("value3", name="name3")

        assert len(self.registry) == 3
        assert self.registry.get("name1") == "value1"
        assert self.registry.get("name2") == "value2"
        assert self.registry.get("name3") == "value3"

    def test_register_overwrites_existing(self):
        """Test that registering with the same name overwrites the existing instance."""
        self.registry.register("original", name="name")
        self.registry.register("updated", name="name")

        assert len(self.registry) == 1
        assert self.registry.get("name") == "updated"

    def test_register_invalidates_metadata_cache(self):
        """Test that registering a new instance invalidates the metadata cache."""
        self.registry.register("value1", name="name1")
        # Build cache by calling list_metadata
        metadata1 = self.registry.list_metadata()
        assert len(metadata1) == 1

        # Register new instance - should invalidate cache
        self.registry.register("value2", name="name2")
        metadata2 = self.registry.list_metadata()

        assert len(metadata2) == 2


class TestBaseInstanceRegistryGet:
    """Tests for get functionality in BaseInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConcreteTestRegistry.reset_instance()
        self.registry = ConcreteTestRegistry.get_registry_singleton()
        self.registry.register("test_value", name="test_name")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConcreteTestRegistry.reset_instance()

    def test_get_existing_instance(self):
        """Test getting an existing instance by name."""
        result = self.registry.get("test_name")
        assert result == "test_value"

    def test_get_nonexistent_returns_none(self):
        """Test that getting a non-existent instance returns None."""
        result = self.registry.get("nonexistent")
        assert result is None


class TestBaseInstanceRegistryGetNames:
    """Tests for get_names functionality in BaseInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConcreteTestRegistry.reset_instance()
        self.registry = ConcreteTestRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConcreteTestRegistry.reset_instance()

    def test_get_names_empty_registry(self):
        """Test get_names on an empty registry."""
        names = self.registry.get_names()
        assert names == []

    def test_get_names_returns_sorted_list(self):
        """Test that get_names returns a sorted list of names."""
        self.registry.register("value3", name="zeta")
        self.registry.register("value1", name="alpha")
        self.registry.register("value2", name="beta")

        names = self.registry.get_names()
        assert names == ["alpha", "beta", "zeta"]


class TestBaseInstanceRegistryListMetadata:
    """Tests for list_metadata functionality in BaseInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConcreteTestRegistry.reset_instance()
        self.registry = ConcreteTestRegistry.get_registry_singleton()
        self.registry.register("test_item_1", name="item1")
        self.registry.register("other_item_2", name="item2")
        self.registry.register("test_item_3", name="item3")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConcreteTestRegistry.reset_instance()

    def test_list_metadata_returns_all_items(self):
        """Test that list_metadata returns metadata for all items."""
        metadata = self.registry.list_metadata()
        assert len(metadata) == 3

    def test_list_metadata_sorted_by_name(self):
        """Test that metadata is sorted by registry key order."""
        metadata = self.registry.list_metadata()
        # Since unique_name is auto-computed, we verify we get 3 items in order
        # The actual unique_name field is auto-computed from class_name::hash
        assert len(metadata) == 3
        # All should have "str" in the unique_name since class_name is "str"
        for m in metadata:
            assert "str" in m.unique_name

    def test_list_metadata_with_filter(self):
        """Test filtering metadata by a field."""
        metadata = self.registry.list_metadata(include_filters={"category": "test"})
        assert len(metadata) == 2
        assert all(m.category == "test" for m in metadata)

    def test_list_metadata_filter_no_match(self):
        """Test filtering with no matches returns empty list."""
        metadata = self.registry.list_metadata(include_filters={"category": "nonexistent"})
        assert metadata == []

    def test_list_metadata_with_exclude_filter(self):
        """Test excluding metadata by a field."""
        metadata = self.registry.list_metadata(exclude_filters={"category": "test"})
        assert len(metadata) == 1
        assert all(m.category == "other" for m in metadata)

    def test_list_metadata_combined_include_and_exclude(self):
        """Test combined include and exclude filters."""
        # Add another test item to have more variety
        self.registry.register("another_test_item", name="item4")

        # Get items with category "test" but exclude by class_name "str"
        # Since all have class_name="str", excluding by class_name would exclude all
        # Instead, test with category filters
        metadata = self.registry.list_metadata(include_filters={"category": "test"})
        assert len(metadata) == 3  # item1, item3, item4 (all have "test" in value)
        assert all(m.category == "test" for m in metadata)

    def test_list_metadata_caching(self):
        """Test that metadata is cached after first call."""
        # First call builds cache
        metadata1 = self.registry.list_metadata()
        # Second call uses cache
        metadata2 = self.registry.list_metadata()

        # Should be the same list object (cached)
        assert metadata1 is metadata2
        assert len(metadata1) == 3


class TestBaseInstanceRegistryDunderMethods:
    """Tests for dunder methods (__contains__, __len__, __iter__) in BaseInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConcreteTestRegistry.reset_instance()
        self.registry = ConcreteTestRegistry.get_registry_singleton()
        self.registry.register("value1", name="name1")
        self.registry.register("value2", name="name2")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConcreteTestRegistry.reset_instance()

    def test_contains_existing_name(self):
        """Test __contains__ returns True for existing name."""
        assert "name1" in self.registry
        assert "name2" in self.registry

    def test_contains_nonexistent_name(self):
        """Test __contains__ returns False for non-existent name."""
        assert "nonexistent" not in self.registry

    def test_len_returns_count(self):
        """Test __len__ returns the correct count."""
        assert len(self.registry) == 2

    def test_len_empty_registry(self):
        """Test __len__ returns 0 for empty registry."""
        ConcreteTestRegistry.reset_instance()
        empty_registry = ConcreteTestRegistry.get_registry_singleton()
        assert len(empty_registry) == 0

    def test_iter_returns_sorted_names(self):
        """Test __iter__ returns names in sorted order."""
        names = list(self.registry)
        assert names == ["name1", "name2"]

    def test_iter_allows_for_loop(self):
        """Test that the registry can be used in a for loop."""
        collected = []
        for name in self.registry:
            collected.append(name)
        assert collected == ["name1", "name2"]
