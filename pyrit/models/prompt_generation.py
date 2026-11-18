# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Prompt generation jobs: their lifecycle, progress, and stored record.

A job runs a prompt generator in the background and saves its result as a new named seed dataset.
The record is generator-neutral: each generator defines the shape of its ``request``, checkpoint,
and result summary.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH

DATASET_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"

DatasetNameStr = Annotated[str, StringConstraints(pattern=DATASET_NAME_PATTERN)]
GeneratorNameStr = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TargetRoleStr = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TargetNameStr = Annotated[str, Field(max_length=MAX_IDENTIFIER_LENGTH)]


class PromptGenerationStatus(str, Enum):
    """Where a generation is in its lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    SAVING = "saving"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_GENERATION_STATUSES = frozenset(
    {PromptGenerationStatus.QUEUED, PromptGenerationStatus.RUNNING, PromptGenerationStatus.SAVING}
)
RETRYABLE_GENERATION_STATUSES = frozenset(
    {PromptGenerationStatus.FAILED, PromptGenerationStatus.CANCELLED, PromptGenerationStatus.INTERRUPTED}
)
# A generation in any other status holds its dataset name, so no other generation or upload can take it.
DATASET_NAME_RELEASING_STATUSES = frozenset({PromptGenerationStatus.COMPLETED, PromptGenerationStatus.CANCELLED})


class TargetObjectiveGenerationRequest(BaseModel):
    """The ``request`` of a ``target_objective`` generation."""

    model_config = ConfigDict(extra="forbid")

    instructions: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
    count: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    harm_categories: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )


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
