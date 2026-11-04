# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset models for the PyRIT API.

Datasets are seed prompt/objective collections provided by
``SeedDatasetProvider`` subclasses. These models describe the wire format for
listing available datasets and browsing their stored seed examples.
"""

import os
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import PromptDataType, SeedRecord, SeedType
from pyrit.models.scenario_dataset import ScenarioDatasetSettings


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
    origins: list[str] = Field(default_factory=list, description="Distinct origins of stored seeds, not providers")
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


@dataclass(frozen=True)
class DatasetUploadLimits:
    """Positive environment-configured limits, in bytes except for prompt count."""

    request_bytes: int = 100 * 1024 * 1024
    manifest_bytes: int = 10 * 1024 * 1024
    prompts: int = 10_000

    @classmethod
    def from_environment(cls) -> "DatasetUploadLimits":
        """
        Read explicit limits without silently accepting invalid configuration.

        Returns:
            DatasetUploadLimits: The positive configured limits.
        """
        values = {
            field: int(os.environ.get(variable, default))
            for field, variable, default in (
                ("request_bytes", "PYRIT_DATASET_MAX_REQUEST_BYTES", cls.request_bytes),
                ("manifest_bytes", "PYRIT_DATASET_MAX_MANIFEST_BYTES", cls.manifest_bytes),
                ("prompts", "PYRIT_DATASET_MAX_PROMPTS", cls.prompts),
            )
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError("Dataset upload limits must be positive integers.")
        return cls(**values)


class DatasetUploadManifest(BaseModel):
    """Untrusted JSON; prompt construction happens only after trust and reference checks."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=256)
    seeds: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value) or len(value.encode("utf-16-le")) > 512:
            raise ValueError("Use a nonempty dataset name of at most 256 UTF-16 code units without outer whitespace.")
        return value


class DatasetUploadResponse(BaseModel):
    """The actual committed population, immediately available through existing browsing."""

    name: str
    selection_key: str
    seed_pieces: int
    logical_examples: int


class DatasetDeletionResponse(BaseModel):
    """Durable status of exact dataset removal and its owned-media cleanup."""

    deletion_id: UUID
    status: Literal["pending", "completed"]
    deleted_seeds: int
    pending_media: int
    error: str | None = None


class ScenarioDatasetLoadResponse(BaseModel):
    """Selected dataset settings and metadata after an explicit load."""

    settings: ScenarioDatasetSettings
    items: list[DatasetInfo]


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


class SeedMemberView(SeedRecord):
    """A stored seed plus the URL a browser can use to fetch its media."""

    value_url: str | None = Field(
        None,
        description=(
            "Client-fetchable media URL (/api/media or a signed blob URL). "
            "Null for non-media. The stored value remains the stable source reference."
        ),
    )


class SeedExampleDetailResponse(SeedExampleSummary):
    """One logical seed example with all of its stored seeds."""

    members: list[SeedMemberView] = Field(
        ...,
        description=(
            "Stored seed records without reconstruction, objectives first, then by sequence. "
            "Media members include value_url for display; value is the stored reference."
        ),
    )
