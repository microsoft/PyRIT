# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Prompt generation jobs: their lifecycle, progress, and stored record.

A job runs a prompt generator in the background and saves its result as a new named seed dataset.
The record is generator-neutral: each generator defines the shape of its ``request``, checkpoint,
and result summary.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, ValidationError

from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH

DATASET_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"

DatasetNameStr = Annotated[str, StringConstraints(pattern=DATASET_NAME_PATTERN)]
GeneratorNameStr = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TargetRoleStr = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TargetNameStr = Annotated[str, Field(max_length=MAX_IDENTIFIER_LENGTH)]


class PromptGenerationStatus(StrEnum):
    """Where a generation is in its lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_GENERATION_STATUSES = frozenset({PromptGenerationStatus.QUEUED, PromptGenerationStatus.RUNNING})
RETRYABLE_GENERATION_STATUSES = frozenset(
    {PromptGenerationStatus.FAILED, PromptGenerationStatus.CANCELLED, PromptGenerationStatus.INTERRUPTED}
)
# A generation in any other status holds its dataset name, so no other generation or upload can take it.
DATASET_NAME_RELEASING_STATUSES = frozenset({PromptGenerationStatus.COMPLETED, PromptGenerationStatus.CANCELLED})


class TargetObjectiveGenerationRequest(BaseModel):
    """
    The request of a ``target_objective`` generation, as ``TargetObjectiveGenerator`` accepts it.

    Generated objectives store the request in their metadata so it can be edited and run again.
    """

    METADATA_KEY: ClassVar[str] = "pyrit_target_objective_request"
    model_config = ConfigDict(extra="forbid")

    instructions: Annotated[str, StringConstraints(pattern=r"\S")]
    count: Annotated[int, Field(strict=True, ge=1)]
    harm_categories: list[Annotated[str, StringConstraints(pattern=r"\S")]] = Field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize this request for seed metadata.

        Returns:
            dict[str, Any]: The metadata fragment that holds this request.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> Self | None:
        """
        Read the request stored in seed metadata.

        Args:
            metadata (Mapping[str, Any] | None): A seed's metadata.

        Returns:
            Self | None: The request, or None if the metadata holds no valid request.
        """
        value = (metadata or {}).get(cls.METADATA_KEY)
        if value is None:
            return None
        try:
            return cls.model_validate(value)
        except ValidationError:
            return None


class PromptGenerationProgress(BaseModel):
    """Completed stages and item counts."""

    completed_stages: list[str] = Field(default_factory=list)
    current_stage: str | None = Field(None, description="The next or running stage, or None when every stage is done")
    items_completed: int = Field(0, ge=0, description="Generated items, such as ASSERT seed groups or objectives")
    items_total: int = Field(0, ge=0)


class PromptGenerationRecord(BaseModel):
    """A stored generation: its inputs, status, resumable progress, and output dataset name."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    generator: GeneratorNameStr
    dataset_name: DatasetNameStr
    status: PromptGenerationStatus
    request: dict[str, JsonValue] = Field(..., description="Generator-specific request, as originally submitted")
    targets: dict[TargetRoleStr, TargetNameStr] = Field(
        default_factory=dict, description="Target registry name for each target role"
    )
    pause_for_review: bool = False
    review_completed: bool = False
    group_id: uuid.UUID | None = Field(None, description="Shared by generations created together in one batch")
    group_label: str | None = None
    group_index: int = Field(0, ge=0, description="Position in the batch request that created this generation")
    progress: PromptGenerationProgress = Field(default_factory=PromptGenerationProgress)
    checkpoint: dict[str, JsonValue] | None = Field(None, description="Generator-owned resumable progress")
    summary: dict[str, JsonValue] | None = Field(None, description="Generator-specific result summary")
    error: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    execution_id: uuid.UUID | None = Field(
        None,
        description="The run attempt that owns an active generation. Its writes require this ID, so a run that "
        "another process cancelled or retried stops instead of overwriting the record.",
    )
