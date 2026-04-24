# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass, field

from pyrit.registry.base import ClassRegistryEntry, _matches_filters


@dataclass(frozen=True)
class MetadataWithTags(ClassRegistryEntry):
    """Test metadata with a tags field for list filtering tests."""

    tags: tuple[str, ...] = field(kw_only=True)


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
