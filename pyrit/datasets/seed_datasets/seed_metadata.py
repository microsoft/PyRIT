# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, ClassVar, Literal, Optional

logger = logging.getLogger(__name__)


"""
Contains metadata objects for datasets (i.e. subclasses of SeedDatasetProvider).

SeedDatasetMetadata is the canonical schema for dataset metadata. All fields are
optional sets. Parsers wrap singular values into single-element sets; filters may
have multiple values per field for OR matching.

SeedDatasetFilter accepts either flat kwargs (simple use) or a list of
SeedDatasetMetadata criteria (composable use). Internally it always stores
criteria as list[SeedDatasetMetadata].
"""

SeedDatasetSizeCategory = Literal["tiny", "small", "medium", "large", "huge"]
# tiny (<10), small (10-99), medium (100-499), large (500-4999), huge (5000+)

SeedDatasetSourceType = Literal["remote", "local"]


class SeedDatasetLoadTime(Enum):
    """Approximate time to load a dataset. Used to skip slow datasets in fast runs."""

    FAST = "fast"
    NORMAL = "normal"
    SLOW = "slow"
    UNINITIALIZED = "uninitialized"


@dataclass(frozen=True)
class SeedDatasetMetadata:
    """
    Unified schema for dataset metadata and filter criteria.

    All fields are optional sets. When used for real dataset metadata, parsers
    wrap singular values into single-element sets. When used as filter criteria,
    multiple values per field express "match any of these" (OR within axis).
    """

    # All fields are optional sets to support both real metadata (single-element)
    # and filter criteria (multi-element). SINGULAR_FIELDS enforces that parsers
    # only produce single-element sets for size and source_type.
    tags: Optional[set[str]] = None
    size: Optional[set[str]] = None
    modalities: Optional[set[str]] = None
    source_type: Optional[set[str]] = None
    load_time: Optional[set[SeedDatasetLoadTime]] = None
    harm_categories: Optional[set[str]] = None

    # Fields that must have at most 1 element in real dataset metadata.
    SINGULAR_FIELDS: ClassVar[frozenset[str]] = frozenset({"size", "source_type"})

    @staticmethod
    def _coerce_metadata_values(*, raw_metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Convert raw values (from YAML or class attributes) into sets for SeedDatasetMetadata.

        Applies .lower().strip() normalization to all string values. Handles str,
        list, set inputs for all fields, plus SeedDatasetLoadTime enum for load_time.

        Args:
            raw_metadata: Dictionary of field names to raw values.

        Returns:
            Dictionary with all values coerced to sets.
        """
        coerced: dict[str, Any] = {}
        for key, value in raw_metadata.items():
            if key == "load_time":
                if isinstance(value, str):
                    coerced[key] = {SeedDatasetLoadTime(value.strip().lower())}
                elif isinstance(value, SeedDatasetLoadTime):
                    coerced[key] = {value}
                else:
                    logger.warning(
                        f"Skipping metadata field '{key}' with unexpected type "
                        f"{type(value).__name__} (value: {value!r})"
                    )
            elif isinstance(value, (list, set)):
                coerced[key] = {v.strip().lower() if isinstance(v, str) else v for v in value}
            elif isinstance(value, str):
                coerced[key] = {value.strip().lower()}
            else:
                logger.warning(
                    f"Skipping metadata field '{key}' with unexpected type {type(value).__name__} (value: {value!r})"
                )
        return coerced

    @staticmethod
    def _validate_singular_fields(*, metadata: "SeedDatasetMetadata") -> None:
        """
        Validate that singular fields have at most 1 element.

        Call this from parsers when constructing real dataset metadata, NOT when
        constructing filter criteria where multiple values are valid.

        Raises:
            ValueError: If a singular field has more than 1 element.
        """
        for field_name in SeedDatasetMetadata.SINGULAR_FIELDS:
            value = getattr(metadata, field_name)
            if value is not None and len(value) > 1:
                raise ValueError(
                    f"Metadata field '{field_name}' must have at most 1 value "
                    f"for real dataset metadata, got {len(value)}: {value}"
                )


class SeedDatasetFilter:
    """
    Filter for discovering datasets by metadata criteria.

    Supports two construction patterns:

    Simple (flat kwargs — wraps into a single SeedDatasetMetadata criterion)::

        f = SeedDatasetFilter(tags={"safety"}, size={"small", "large"})

    Composed (explicit criteria list — OR across criteria, AND within each)::

        f = SeedDatasetFilter(criteria=[
            SeedDatasetMetadata(size={"small"}, modalities={"text"}),
            SeedDatasetMetadata(size={"large"}, modalities={"image"}),
        ])

    Passing both flat kwargs and criteria raises ValueError.

    Special tags:
    - "all": Returns every dataset, ignores all other fields. This tag will
       override anything else you pass to the filter object.
    - "default": Matches datasets with "default" in their tags. With
      strict_match=True, loses its shortcut and is treated as a normal tag.

    Args:
        criteria: Explicit list of SeedDatasetMetadata to OR-match against.
        strict_match: If True, within-axis matching uses AND (all filter values
            must be present) instead of OR (any overlap suffices).
        **kwargs: Flat metadata fields (tags, size, modalities, etc.) for simple use.
    """

    def __init__(
        self,
        *,
        criteria: Optional[list[SeedDatasetMetadata]] = None,
        strict_match: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Construct a filter from flat metadata kwargs or an explicit criteria list.

        Simple usage (flat kwargs — wraps into a single SeedDatasetMetadata)::

            f = SeedDatasetFilter(tags={"safety"}, size={"small", "large"})

        Composed usage (explicit criteria — OR across criteria, AND within each)::

            f = SeedDatasetFilter(criteria=[
                SeedDatasetMetadata(size={"small"}, modalities={"text"}),
                SeedDatasetMetadata(size={"large"}, modalities={"image"}),
            ])

        Args:
            criteria: Explicit list of SeedDatasetMetadata to OR-match against.
            strict_match: If True, within-axis matching uses AND instead of OR.
            **kwargs: Flat metadata fields passed to SeedDatasetMetadata.

        Raises:
            ValueError: If both criteria and flat kwargs are provided.
        """
        if criteria is not None and kwargs:
            raise ValueError("Cannot pass both 'criteria' and flat metadata kwargs. Use one or the other.")

        if criteria is not None:
            self.criteria = criteria
        elif kwargs:
            self.criteria = [SeedDatasetMetadata(**kwargs)]
        else:
            self.criteria = [SeedDatasetMetadata()]

        # Normalize tags: strip whitespace and lowercase so "ALL", " All ", etc. work
        def _normalize_criterion(c: SeedDatasetMetadata) -> SeedDatasetMetadata:
            normalized = {
                f.name: ({t.strip().lower() for t in vals} if f.name == "tags" and vals is not None else vals)
                for f, vals in zip(fields(c), (getattr(c, f.name) for f in fields(c)), strict=True)
            }
            return SeedDatasetMetadata(**normalized)

        self.criteria = [_normalize_criterion(c) for c in self.criteria]

        self.strict_match = strict_match
        self._validate()

    def _validate(self) -> None:
        """
        Warn about contradictory filter configurations.

        Raises:
            ValueError: If strict_match is True and any criterion has multiple
                values for a singular field (size, source_type).
        """
        # strict_match with multi-valued singular fields is logically impossible.
        # A dataset can't be both "small" AND "large" — these are mutually exclusive.
        if self.strict_match:
            for criterion in self.criteria:
                for field_name in SeedDatasetMetadata.SINGULAR_FIELDS:
                    value = getattr(criterion, field_name)
                    if value is not None and len(value) > 1:
                        raise ValueError(
                            f"strict_match=True with multiple values for '{field_name}' "
                            f"({value}) is logically impossible — a dataset can only have "
                            f"one {field_name}. Mutually exclusive fields: "
                            f"{SeedDatasetMetadata.SINGULAR_FIELDS}. "
                            f"Use strict_match=False for OR matching, "
                            f"or split into separate criteria."
                        )

        if not self.has_all_tag:
            return

        all_criterion = next(c for c in self.criteria if c.tags and "all" in c.tags)

        if all_criterion.tags and len(all_criterion.tags) > 1:
            logger.warning(
                "Filter has 'all' combined with other tags %s. "
                "'all' bypasses all filtering — other tags will be ignored.",
                all_criterion.tags - {"all"},
            )
        if self.strict_match:
            logger.warning(
                "Filter has 'all' with strict_match=True. 'all' bypasses all filtering — strict_match has no effect."
            )
        other_fields = [
            f.name for f in fields(all_criterion) if f.name != "tags" and getattr(all_criterion, f.name) is not None
        ]
        if other_fields:
            logger.warning(
                "Filter has 'all' combined with other filter fields %s. "
                "'all' bypasses all filtering — other fields will be ignored.",
                other_fields,
            )

    @property
    def has_all_tag(self) -> bool:
        """True if any criterion has the 'all' tag."""
        return any(c.tags and "all" in c.tags for c in self.criteria)
