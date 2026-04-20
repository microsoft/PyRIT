# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass

import pytest

from pyrit.identifiers import Identifier
from pyrit.registry.base import _matches_filters


@dataclass(frozen=True)
class MetadataWithTags(Identifier):
    """Test metadata with a tags field for list filtering tests."""

    tags: tuple[str, ...]


class TestMatchesFilters:
    """Tests for the _matches_filters function."""

    def test_matches_filters_exact_match_string(self):
        """Test that exact string matches work."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, include_filters={"class_name": "TestClass"}) is True
        assert _matches_filters(metadata, include_filters={"class_module": "test.module"}) is True

    def test_matches_filters_no_match_string(self):
        """Test that non-matching strings return False."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, include_filters={"class_name": "OtherClass"}) is False
        assert _matches_filters(metadata, include_filters={"class_module": "other.module"}) is False

    def test_matches_filters_multiple_filters_all_match(self):
        """Test that all filters must match."""
        metadata = Identifier(
            identifier_type="class",
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
        metadata = Identifier(
            identifier_type="class",
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
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, include_filters={"nonexistent_key": "value"}) is False

    def test_matches_filters_empty_filters(self):
        """Test that empty filters return True."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata) is True

    def test_matches_filters_list_value_contains_filter(self):
        """Test filtering when metadata value is a list and filter value is in the list."""
        metadata = MetadataWithTags(
            identifier_type="class",
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
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
            tags=("tag1", "tag2", "tag3"),
        )
        assert _matches_filters(metadata, include_filters={"tags": "missing_tag"}) is False

    def test_matches_filters_exclude_exact_match(self):
        """Test that exclude filters work for exact matches."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        assert _matches_filters(metadata, exclude_filters={"class_name": "TestClass"}) is False
        assert _matches_filters(metadata, exclude_filters={"class_name": "OtherClass"}) is True

    def test_matches_filters_exclude_list_value(self):
        """Test exclude filters work for list values."""
        metadata = MetadataWithTags(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
            tags=("tag1", "tag2", "tag3"),
        )
        assert _matches_filters(metadata, exclude_filters={"tags": "tag1"}) is False
        assert _matches_filters(metadata, exclude_filters={"tags": "missing_tag"}) is True

    def test_matches_filters_exclude_nonexistent_key(self):
        """Test that exclude filters for non-existent keys don't exclude the item."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test item",
        )
        # Non-existent key in exclude filter should not exclude the item
        assert _matches_filters(metadata, exclude_filters={"nonexistent_key": "value"}) is True

    def test_matches_filters_combined_include_and_exclude(self):
        """Test combined include and exclude filters."""
        metadata = Identifier(
            identifier_type="class",
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


class TestIdentifier:
    """Tests for the Identifier dataclass and hash computation."""

    def test_identifier_creation(self):
        """Test creating an Identifier instance."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestScorer",
            class_module="pyrit.test.scorer",
            class_description="A test scorer for testing",
        )
        assert metadata.identifier_type == "class"
        assert metadata.class_name == "TestScorer"
        assert metadata.class_module == "pyrit.test.scorer"
        assert metadata.class_description == "A test scorer for testing"
        # unique_name is auto-computed
        assert metadata.unique_name is not None
        assert "test_scorer" in metadata.unique_name

    def test_identifier_is_frozen(self):
        """Test that Identifier is immutable."""
        metadata = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="Description here",
        )

        with pytest.raises(AttributeError):
            metadata.unique_name = "new_name"  # type: ignore[misc]

    def test_identifier_hash_computed_at_creation(self):
        """Test that hash is computed when the Identifier is created."""
        identifier = Identifier(
            identifier_type="instance",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test description",
        )
        assert identifier.hash is not None
        assert len(identifier.hash) == 64  # SHA256 hex length

    def test_identifier_hash_is_deterministic(self):
        """Test that the same inputs produce the same hash."""
        identifier1 = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test description",
        )
        identifier2 = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test description",
        )
        assert identifier1.hash == identifier2.hash

    def test_identifier_hash_differs_for_different_inputs(self):
        """Test that different inputs produce different hashes."""
        identifier1 = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test description",
        )
        identifier2 = Identifier(
            identifier_type="class",
            class_name="DifferentClass",
            class_module="test.module",
            class_description="A test description",
        )
        assert identifier1.hash != identifier2.hash

    def test_identifier_hash_is_immutable(self):
        """Test that the hash cannot be modified."""
        identifier = Identifier(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test description",
        )
        with pytest.raises(AttributeError):
            identifier.hash = "new_hash"  # type: ignore[misc]

    def test_identifier_subclass_inherits_hash(self):
        """Test that subclasses of Identifier also get a computed hash."""
        metadata = MetadataWithTags(
            identifier_type="class",
            class_name="TestClass",
            class_module="test.module",
            class_description="A test description",
            tags=("tag1", "tag2"),
        )
        assert metadata.hash is not None
        assert len(metadata.hash) == 64
