# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for metadata components related to SeedDatasetProvider.
"""

import pytest

from pyrit.datasets.seed_datasets.seed_metadata import (
    SeedDatasetFilter,
    SeedDatasetLoadTime,
    SeedDatasetMetadata,
)


class TestMetadataLifecycle:
    """Test that the metadata object can be created with different subsets of values."""

    def test_has_no_values(self):
        metadata = SeedDatasetMetadata()
        assert metadata.tags is None
        assert metadata.size is None
        assert metadata.modalities is None
        assert metadata.source_type is None
        assert metadata.load_time is None
        assert metadata.harm_categories is None

    def test_has_some_values(self):
        metadata = SeedDatasetMetadata(tags={"safety"}, size={"large"})
        assert metadata.tags == {"safety"}
        assert metadata.size == {"large"}
        assert metadata.modalities is None

    def test_has_all_values(self):
        metadata = SeedDatasetMetadata(
            tags={"default", "safety"},
            size={"medium"},
            modalities={"text", "image"},
            source_type={"remote"},
            load_time={SeedDatasetLoadTime.FAST},
            harm_categories={"violence", "illegal"},
        )
        assert metadata.tags == {"default", "safety"}
        assert metadata.size == {"medium"}
        assert len(metadata.modalities) == 2
        assert metadata.source_type == {"remote"}
        assert SeedDatasetLoadTime.FAST in metadata.load_time
        assert metadata.harm_categories == {"violence", "illegal"}


class TestFilterLifecycle:
    """Test that the filter object wraps metadata correctly."""

    def test_has_no_values(self):
        f = SeedDatasetFilter()
        c = f.criteria[0]
        assert c.tags is None
        assert c.size is None

    def test_has_some_values(self):
        f = SeedDatasetFilter(size={"large"})
        assert f.criteria[0].size == {"large"}
        assert f.criteria[0].tags is None

    def test_has_all_values(self):
        f = SeedDatasetFilter(
            tags={"default"},
            size={"small", "medium"},
            modalities={"text"},
            source_type={"remote"},
            load_time={SeedDatasetLoadTime.FAST},
            harm_categories={"violence"},
        )
        c = f.criteria[0]
        assert c.tags == {"default"}
        assert len(c.size) == 2
        assert c.modalities == {"text"}

    def test_filter_allows_multiple_sizes(self):
        """Filters can have multiple values for singular fields like size."""
        f = SeedDatasetFilter(size={"small", "medium", "large"})
        assert len(f.criteria[0].size) == 3


class TestMetadataProperties:
    """Test that the metadata fields populate correctly."""

    def test_size_value(self):
        for size in ["tiny", "small", "medium", "large", "huge"]:
            metadata = SeedDatasetMetadata(size={size})
            assert size in metadata.size

    def test_load_time_value(self):
        for lt in SeedDatasetLoadTime:
            metadata = SeedDatasetMetadata(load_time={lt})
            assert lt in metadata.load_time

    def test_source_value(self):
        for source_type in ["remote", "local"]:
            metadata = SeedDatasetMetadata(source_type={source_type})
            assert source_type in metadata.source_type

    def test_modality_value(self):
        for modality in ["text", "image", "video", "audio"]:
            metadata = SeedDatasetMetadata(modalities={modality})
            assert modality in metadata.modalities

    def test_tags_value(self):
        metadata = SeedDatasetMetadata(tags={"safety", "default", "custom"})
        assert "safety" in metadata.tags

    def test_harm_categories_value(self):
        metadata = SeedDatasetMetadata(harm_categories={"violence", "cybercrime"})
        assert "violence" in metadata.harm_categories


class TestMetadataCoercion:
    """Test that _coerce_metadata_values normalizes raw values into sets."""

    def test_tags_list_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"tags": ["safety", "default"]})
        assert result["tags"] == {"safety", "default"}
        assert isinstance(result["tags"], set)

    def test_tags_string_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"tags": "safety"})
        assert result["tags"] == {"safety"}

    def test_tags_normalized_lower_strip(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"tags": ["  Safety ", " DEFAULT"]})
        assert result["tags"] == {"safety", "default"}

    def test_size_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"size": " Large "})
        assert result["size"] == {"large"}
        assert isinstance(result["size"], set)

    def test_source_type_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"source_type": " Remote "})
        assert result["source_type"] == {"remote"}

    def test_load_time_coerced_to_enum_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"load_time": "fast"})
        assert result["load_time"] == {SeedDatasetLoadTime.FAST}
        assert isinstance(result["load_time"], set)

    def test_load_time_normalized_strip_lower(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"load_time": " Slow "})
        assert result["load_time"] == {SeedDatasetLoadTime.SLOW}

    def test_modalities_list_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"modalities": ["Text", " IMAGE "]})
        assert result["modalities"] == {"text", "image"}

    def test_modalities_string_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"modalities": "text"})
        assert result["modalities"] == {"text"}

    def test_harm_categories_list_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(
            raw_metadata={"harm_categories": ["Violence", " Cybercrime "]}
        )
        assert result["harm_categories"] == {"violence", "cybercrime"}

    def test_harm_categories_string_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"harm_categories": "violence"})
        assert result["harm_categories"] == {"violence"}

    def test_unknown_type_skipped_with_warning(self, caplog):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"tags": 12345})
        assert "tags" not in result
        assert "Skipping metadata field" in caplog.text


class TestSingularFieldValidation:
    """Test that singular fields (size, source_type) are validated."""

    def test_singular_size_passes(self):
        metadata = SeedDatasetMetadata(size={"large"})
        SeedDatasetMetadata._validate_singular_fields(metadata=metadata)

    def test_singular_source_type_passes(self):
        metadata = SeedDatasetMetadata(source_type={"remote"})
        SeedDatasetMetadata._validate_singular_fields(metadata=metadata)

    def test_multiple_sizes_fails(self):
        metadata = SeedDatasetMetadata(size={"small", "large"})
        with pytest.raises(ValueError, match="size"):
            SeedDatasetMetadata._validate_singular_fields(metadata=metadata)

    def test_multiple_source_types_fails(self):
        metadata = SeedDatasetMetadata(source_type={"remote", "local"})
        with pytest.raises(ValueError, match="source_type"):
            SeedDatasetMetadata._validate_singular_fields(metadata=metadata)

    def test_none_fields_pass(self):
        metadata = SeedDatasetMetadata()
        SeedDatasetMetadata._validate_singular_fields(metadata=metadata)

    def test_multi_value_non_singular_fields_pass(self):
        """Tags, modalities, harm_categories can have multiple values."""
        metadata = SeedDatasetMetadata(
            tags={"safety", "default"},
            modalities={"text", "image"},
            harm_categories={"violence", "cybercrime"},
        )
        SeedDatasetMetadata._validate_singular_fields(metadata=metadata)


class TestStrictMatchSingularFieldValidation:
    """
    Test that strict_match rejects multi-valued singular fields.

    A dataset can't be both "small" AND "large" — these are mutually exclusive.
    strict_match=True with size={"small", "large"} is logically impossible
    and should raise ValueError at filter construction time.
    """

    def test_strict_multi_size_raises(self):
        """strict_match with size={'small', 'large'} is impossible."""
        with pytest.raises(ValueError, match="logically impossible"):
            SeedDatasetFilter(size={"small", "large"}, strict_match=True)

    def test_strict_multi_source_type_raises(self):
        """strict_match with source_type={'remote', 'local'} is impossible."""
        with pytest.raises(ValueError, match="logically impossible"):
            SeedDatasetFilter(source_type={"remote", "local"}, strict_match=True)

    def test_strict_single_size_ok(self):
        """strict_match with single size value is fine."""
        f = SeedDatasetFilter(size={"large"}, strict_match=True)
        assert f.criteria[0].size == {"large"}

    def test_nonstrict_multi_size_ok(self):
        """Without strict_match, multiple sizes is OR and perfectly valid."""
        f = SeedDatasetFilter(size={"small", "large"}, strict_match=False)
        assert len(f.criteria[0].size) == 2

    def test_strict_multi_tags_ok(self):
        """Tags are NOT singular — strict with multiple tags is valid (AND)."""
        f = SeedDatasetFilter(tags={"safety", "default"}, strict_match=True)
        assert len(f.criteria[0].tags) == 2

    def test_strict_multi_harm_categories_ok(self):
        """harm_categories are NOT singular — strict with multiple is valid."""
        f = SeedDatasetFilter(harm_categories={"violence", "cybercrime"}, strict_match=True)
        assert len(f.criteria[0].harm_categories) == 2

    def test_strict_criteria_list_multi_size_raises(self):
        """strict_match validation also applies to criteria=[] construction."""
        with pytest.raises(ValueError, match="logically impossible"):
            SeedDatasetFilter(
                criteria=[SeedDatasetMetadata(size={"small", "large"})],
                strict_match=True,
            )


class TestFilterProperties:
    """Test that the filter fields populate correctly via flat kwargs."""

    def test_sizes_values(self):
        f = SeedDatasetFilter(size={"small", "large"})
        assert "small" in f.criteria[0].size
        assert "large" in f.criteria[0].size

    def test_load_times_values(self):
        f = SeedDatasetFilter(load_time={SeedDatasetLoadTime.FAST, SeedDatasetLoadTime.SLOW})
        assert SeedDatasetLoadTime.FAST in f.criteria[0].load_time

    def test_sources_values(self):
        f = SeedDatasetFilter(source_type={"local", "remote"})
        assert "local" in f.criteria[0].source_type

    def test_modalities_values(self):
        f = SeedDatasetFilter(modalities={"text", "image"})
        assert "text" in f.criteria[0].modalities

    def test_tags_values(self):
        f = SeedDatasetFilter(tags={"safety", "default"})
        assert "safety" in f.criteria[0].tags

    def test_harm_categories_values(self):
        f = SeedDatasetFilter(harm_categories={"violence", "cybercrime"})
        assert "violence" in f.criteria[0].harm_categories
