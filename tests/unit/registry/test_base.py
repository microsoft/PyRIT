# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass, field

import pytest

from pyrit.registry.base import ClassRegistryEntry, _matches_filters
from pyrit.registry.class_registries.base_class_registry import BaseClassRegistry, ClassEntry


@dataclass(frozen=True)
class MetadataWithTags(ClassRegistryEntry):
    """Test metadata with a tags field for list filtering tests."""

    tags: tuple[str, ...] = field(kw_only=True)


class _TestRegistry(BaseClassRegistry[object, ClassRegistryEntry]):
    """Minimal concrete registry for testing BaseClassRegistry methods."""

    def _discover(self) -> None:
        pass

    def _build_metadata(self, name: str, entry: ClassEntry[object]) -> ClassRegistryEntry:
        return ClassRegistryEntry(
            class_name=entry.registered_class.__name__,
            class_module=entry.registered_class.__module__,
            class_description=entry.get_description(fallback=""),
            registry_name=name,
        )


class TestDescriptionFromDocstring:
    """Tests for ClassRegistryEntry.description_from_docstring."""

    def test_extracts_docstring_and_normalizes_whitespace(self):
        class MyClass:
            """This  is\n  a   docstring."""

        result = ClassRegistryEntry.description_from_docstring(MyClass)
        assert result == "This is a docstring."

    def test_returns_fallback_when_no_docstring(self):
        class NoDoc:
            pass

        result = ClassRegistryEntry.description_from_docstring(NoDoc, fallback="default")
        assert result == "default"

    def test_returns_fallback_when_empty_docstring(self):
        class EmptyDoc:
            """ """

        result = ClassRegistryEntry.description_from_docstring(EmptyDoc, fallback="fallback")
        assert result == "fallback"

    def test_returns_empty_string_when_no_docstring_and_no_fallback(self):
        class NoDoc:
            pass

        result = ClassRegistryEntry.description_from_docstring(NoDoc)
        assert result == ""


class TestClassEntryGetDescription:
    """Tests for ClassEntry.get_description."""

    def test_returns_docstring_description(self):
        class Documented:
            """A documented class."""

        entry = ClassEntry(registered_class=Documented)
        assert entry.get_description() == "A documented class."

    def test_returns_fallback_when_no_docstring(self):
        class Undocumented:
            pass

        entry = ClassEntry(registered_class=Undocumented)
        assert entry.get_description(fallback="No description available") == "No description available"


class TestMatchesFilters:
    """Tests for the _matches_filters function."""

    def test_matches_filters_exact_match_string(self):
        """Test that exact string matches work."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, include_filters={"class_name": "TestClass"}) is True
        assert _matches_filters(metadata, include_filters={"class_module": "test.module"}) is True

    def test_matches_filters_no_match_string(self):
        """Test that non-matching strings return False."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, include_filters={"class_name": "OtherClass"}) is False
        assert _matches_filters(metadata, include_filters={"class_module": "other.module"}) is False

    def test_matches_filters_multiple_filters_all_match(self):
        """Test that all filters must match."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert (
            _matches_filters(metadata, include_filters={"class_name": "TestClass", "class_module": "test.module"})
            is True
        )

    def test_matches_filters_multiple_filters_partial_match(self):
        """Test that partial matches return False when not all filters match."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert (
            _matches_filters(metadata, include_filters={"class_name": "TestClass", "class_module": "other.module"})
            is False
        )

    def test_matches_filters_key_not_in_metadata(self):
        """Test that filtering on a non-existent key returns False."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, include_filters={"nonexistent_key": "value"}) is False

    def test_matches_filters_empty_filters(self):
        """Test that empty filters return True."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata) is True

    def test_matches_filters_list_value_contains_filter(self):
        """Test filtering when metadata value is a list and filter value is in the list."""
        metadata = MetadataWithTags(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
            tags=("tag1", "tag2", "tag3"),
        )
        assert _matches_filters(metadata, include_filters={"tags": "tag1"}) is True
        assert _matches_filters(metadata, include_filters={"tags": "tag2"}) is True

    def test_matches_filters_list_value_not_contains_filter(self):
        """Test filtering when metadata value is a list and filter value is not in the list."""
        metadata = MetadataWithTags(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
            tags=("tag1", "tag2", "tag3"),
        )
        assert _matches_filters(metadata, include_filters={"tags": "missing_tag"}) is False

    def test_matches_filters_exclude_exact_match(self):
        """Test that exclude filters work for exact matches."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, exclude_filters={"class_name": "TestClass"}) is False
        assert _matches_filters(metadata, exclude_filters={"class_name": "OtherClass"}) is True

    def test_matches_filters_exclude_list_value(self):
        """Test exclude filters work for list values."""
        metadata = MetadataWithTags(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
            tags=("tag1", "tag2", "tag3"),
        )
        assert _matches_filters(metadata, exclude_filters={"tags": "tag1"}) is False
        assert _matches_filters(metadata, exclude_filters={"tags": "missing_tag"}) is True

    def test_matches_filters_exclude_nonexistent_key(self):
        """Test that exclude filters for non-existent keys don't exclude the item."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        # Non-existent key in exclude filter should not exclude the item
        assert _matches_filters(metadata, exclude_filters={"nonexistent_key": "value"}) is True

    def test_matches_filters_combined_include_and_exclude(self):
        """Test combined include and exclude filters."""
        metadata = ClassRegistryEntry(
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        # Include matches, exclude doesn't -> should pass
        assert (
            _matches_filters(
                metadata, include_filters={"class_name": "TestClass"}, exclude_filters={"class_module": "other.module"}
            )
            is True
        )
        # Include matches, exclude also matches -> should fail
        assert (
            _matches_filters(
                metadata, include_filters={"class_name": "TestClass"}, exclude_filters={"class_module": "test.module"}
            )
            is False
        )
        # Include doesn't match, exclude doesn't match -> should fail (include takes precedence)
        assert (
            _matches_filters(
                metadata, include_filters={"class_name": "OtherClass"}, exclude_filters={"class_module": "other.module"}
            )
            is False
        )


# ============================================================================
# BaseClassRegistry.unregister Tests
# ============================================================================


class _DummyClass:
    """A dummy class for registry testing."""


class _AnotherClass:
    """Another dummy class."""


def test_unregister_removes_entry():
    """Test that unregister removes a registered entry."""
    registry = _TestRegistry(lazy_discovery=True)
    registry.register(_DummyClass, name="dummy")
    assert "dummy" in registry

    registry.unregister("dummy")
    assert "dummy" not in registry
    assert len(registry) == 0


def test_unregister_raises_key_error_for_missing():
    """Test that unregister raises KeyError when name is not registered."""
    registry = _TestRegistry(lazy_discovery=True)

    with pytest.raises(KeyError, match="not_here"):
        registry.unregister("not_here")


def test_unregister_key_error_lists_available_names():
    """Test that the KeyError message includes available names."""
    registry = _TestRegistry(lazy_discovery=True)
    registry.register(_DummyClass, name="alpha")
    registry.register(_AnotherClass, name="beta")

    with pytest.raises(KeyError, match="alpha"):
        registry.unregister("missing")


def test_unregister_invalidates_metadata_cache():
    """Test that unregister clears the metadata cache."""
    registry = _TestRegistry(lazy_discovery=True)
    registry.register(_DummyClass, name="cached")

    registry.list_metadata()
    assert registry._metadata_cache is not None

    registry.unregister("cached")
    assert registry._metadata_cache is None


def test_unregister_does_not_affect_other_entries():
    """Test that unregistering one entry leaves others intact."""
    registry = _TestRegistry(lazy_discovery=True)
    registry.register(_DummyClass, name="keep")
    registry.register(_AnotherClass, name="remove")

    registry.unregister("remove")

    assert "keep" in registry
    assert "remove" not in registry
    assert registry.get_class("keep") is _DummyClass


def test_unregister_then_re_register():
    """Test that an entry can be re-registered after being unregistered."""
    registry = _TestRegistry(lazy_discovery=True)
    registry.register(_DummyClass, name="reuse")

    registry.unregister("reuse")
    assert "reuse" not in registry

    registry.register(_AnotherClass, name="reuse")
    assert registry.get_class("reuse") is _AnotherClass


def test_unregister_makes_metadata_reflect_removal():
    """Test that list_metadata no longer includes the unregistered entry."""
    registry = _TestRegistry(lazy_discovery=True)
    registry.register(_DummyClass, name="alpha")
    registry.register(_AnotherClass, name="beta")

    assert len(registry.list_metadata()) == 2

    registry.unregister("alpha")
    metadata = registry.list_metadata()

    assert len(metadata) == 1
    assert metadata[0].registry_name == "beta"
