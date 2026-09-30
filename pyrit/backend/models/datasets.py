# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset models for the PyRIT API.

Datasets are seed prompt/objective collections provided by
``SeedDatasetProvider`` subclasses. These models describe the wire format for
listing available datasets.
"""

from pydantic import BaseModel, Field


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
