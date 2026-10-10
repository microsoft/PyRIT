# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset models for the PyRIT API.

Datasets are seed prompt/objective collections provided by
``SeedDatasetProvider`` subclasses. These models describe the wire format for
listing available datasets and browsing their stored seed examples.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import PromptDataType, SeedRecord, SeedType


class DatasetInfo(BaseModel):
    """Metadata about a single available dataset."""

    name: str = Field(..., description="Dataset name (e.g., 'harmbench')")
    selection_key: str = Field(
        ...,
        description=(
            "Stable key used to select this dataset; named datasets use 'dataset:named:<name>' "
            "and the unnamed population uses 'dataset:unnamed'"
        ),
    )
    loaded: bool = Field(..., description="Whether this dataset currently has seeds in memory")
    provider_available: bool = Field(..., description="Whether a registered provider can load this dataset")
    logical_examples: int | None = Field(
        None, description="Number of logical examples represented by stored seed groups"
    )
    seed_pieces: int | None = Field(None, description="Number of stored seed/prompt pieces")
    objectives: int | None = Field(None, description="Number of stored objective seeds")
    modalities: list[str] = Field(default_factory=list, description="Distinct stored data types")
    harm_categories: list[str] = Field(default_factory=list, description="Distinct stored harm categories")
    has_unlabeled_harm_categories: bool = Field(
        False, description="Whether any stored seed is missing a harm-category label"
    )


class DatasetListResponse(BaseModel):
    """Response for listing available datasets."""

    items: list[DatasetInfo] = Field(..., description="List of available datasets")


class SeedExampleSummary(BaseModel):
    """One logical seed example: the seeds that share a group ID, or one seed without a group."""

    example_id: UUID = Field(..., description="The prompt_group_id, or the seed ID of a seed without a group")
    name: str | None = Field(None, description="The first member name, if any")
    preview: str = Field(..., description="Text preview of at most 100 characters, or a type label")
    preview_truncated: bool = Field(..., description="Whether the preview text was shortened")
    modalities: list[PromptDataType]
    seed_types: list[SeedType]
    piece_count: int
    objective_count: int
    harm_categories: list[str]
    has_unlabeled_harm: bool = Field(..., description="Whether any member has no harm category")


class SeedExampleListResponse(BaseModel):
    """One page of logical seed examples."""

    items: list[SeedExampleSummary]
    pagination: PaginationInfo
    total: int = Field(..., description="Number of logical examples that match the filters")


class SeedExampleDetailResponse(SeedExampleSummary):
    """One logical seed example with all of its stored seeds."""

    members: list[SeedRecord] = Field(
        ..., description="Stored seed records without reconstruction, objectives first, then by sequence"
    )
