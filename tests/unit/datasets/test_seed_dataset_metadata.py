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

    def test_empty_criteria_fails(self):
        with pytest.raises(ValueError, match="criteria.*at least one"):
            SeedDatasetFilter(criteria=[])

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

    def test_frozenset_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"tags": frozenset({"Default", "Safety"})})
        assert result["tags"] == {"default", "safety"}

    def test_tuple_coerced_to_set(self):
        result = SeedDatasetMetadata._coerce_metadata_values(raw_metadata={"modalities": ("Image", "Text")})
        assert result["modalities"] == {"image", "text"}

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


class TestEmptySetAxisValidation:
    """
    Test that a filter axis given an empty set is rejected.

    ``None`` means the axis is not requested. An empty set is not the same thing: it
    matches no dataset without strict_match and every dataset with it, so one flag
    would decide whether the filter returns nothing or everything.
    """

    def test_empty_tags_raises(self):
        with pytest.raises(ValueError, match="empty set"):
            SeedDatasetFilter(tags=set())

    def test_empty_harm_categories_strict_raises(self):
        with pytest.raises(ValueError, match="empty set"):
            SeedDatasetFilter(harm_categories=set(), strict_match=True)

    def test_empty_axis_in_criteria_list_raises(self):
        with pytest.raises(ValueError, match="empty set"):
            SeedDatasetFilter(criteria=[SeedDatasetMetadata(modalities=set())])

    def test_all_empty_axes_are_reported_at_once(self):
        """Every empty axis is named in one sorted message, not just the first one found."""
        with pytest.raises(ValueError, match=r"Filter axes \['harm_categories', 'modalities'\]"):
            SeedDatasetFilter(criteria=[SeedDatasetMetadata(modalities=set(), harm_categories=set())])

    def test_all_tag_keeps_an_empty_axis_accepted(self):
        """'all' bypasses every other field, so an empty axis beside it is ignored, not rejected."""
        f = SeedDatasetFilter(tags={"all"}, harm_categories=set())
        assert f.has_all_tag
        assert f.criteria[0].harm_categories == set()

    @pytest.mark.parametrize("strict_match", [True, False])
    def test_all_tag_in_one_criterion_covers_an_empty_axis_in_another(self, strict_match):
        """Composed criteria: one asks for everything, another has an empty axis."""
        f = SeedDatasetFilter(
            criteria=[
                SeedDatasetMetadata(tags={"all"}),
                SeedDatasetMetadata(modalities=set()),
            ],
            strict_match=strict_match,
        )
        assert f.has_all_tag

    def test_all_tag_keeps_multi_valued_singular_fields(self):
        """'all' leaves strict_match nothing to decide, so it must not raise on it.

        size is singular, so {"small", "large"} under strict_match is rejected
        everywhere else. Beside 'all' the tag has already bypassed the axis, and
        the warning below says strict_match has no effect, so raising would
        contradict the warning the caller is about to be given.
        """
        f = SeedDatasetFilter(tags={"all"}, size={"small", "large"}, strict_match=True)
        assert f.has_all_tag
        assert f.criteria[0].size == {"small", "large"}

    def test_multi_valued_singular_fields_still_raise_without_all(self):
        """The control: the same input without 'all' is still rejected."""
        with pytest.raises(ValueError, match="logically impossible"):
            SeedDatasetFilter(size={"small", "large"}, strict_match=True)

    def test_empty_axis_message_covers_both_strict_match_outcomes(self):
        """The message must not claim 'matches no dataset' unconditionally.

        With strict_match an empty set matches every dataset that declares the
        axis, so the old wording was wrong for half the flag's values.
        """
        with pytest.raises(ValueError) as exc:
            SeedDatasetFilter(modalities=set(), strict_match=True)
        message = str(exc.value)
        assert "matches no dataset" in message
        assert "every dataset that declares the axis" in message

    def test_none_axis_is_still_accepted(self):
        """None keeps its meaning: the axis is not requested."""
        f = SeedDatasetFilter(size=None, harm_categories=None, strict_match=True)
        assert f.criteria[0].size is None
        assert f.criteria[0].harm_categories is None


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
