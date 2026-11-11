# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST models for prompt generation: running generator requests as background jobs, reviewing
intermediate output, and saving the result as a named seed dataset.

The record is generator-neutral. Each generator defines the shape of its ``request``, its review
payload, and its result summary; ``GET /prompt-generators`` describes the registered generators.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from pyrit.backend.models.common import IdentifierStr
from pyrit.models import PromptGenerationEstimate

DATASET_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"

DatasetNameStr = Annotated[str, StringConstraints(pattern=DATASET_NAME_PATTERN)]
GeneratorNameStr = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TargetRoleStr = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


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


class PromptGeneratorStage(BaseModel):
    """One stage a generator runs, in order."""

    name: str
    label: str
    reviewable: bool = Field(False, description="Whether generation can pause after this stage for review")


class PromptGeneratorTargetRole(BaseModel):
    """A target a generator uses, chosen from the target registry."""

    role: str
    label: str
    description: str
    required: bool


class PromptGeneratorDescriptor(BaseModel):
    """A registered generator and what it needs."""

    name: str = Field(..., description="Stable generator key, used as ``generator`` in requests")
    display_name: str
    description: str
    stages: list[PromptGeneratorStage]
    target_roles: list[PromptGeneratorTargetRole]
    follow_up_scenario: str | None = Field(
        None, description="A scenario that runs datasets from this generator, preselected after completion"
    )


class PromptGeneratorListResponse(BaseModel):
    """The registered generators."""

    items: list[PromptGeneratorDescriptor]


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
    """
    A stored generation: its inputs, status, resumable progress, and output dataset name.

    Stored as one JSON document in the memory's results storage.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    generator: GeneratorNameStr
    dataset_name: DatasetNameStr
    status: PromptGenerationStatus
    request: dict[str, JsonValue] = Field(..., description="Generator-specific request, as originally submitted")
    targets: dict[TargetRoleStr, IdentifierStr] = Field(
        default_factory=dict, description="Target registry name for each target role"
    )
    pause_for_review: bool = False
    review_completed: bool = False
    group_id: str | None = Field(None, description="Shared by generations created together in one batch")
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


class PromptGenerationSummary(BaseModel):
    """A generation without its request and checkpoint, for listings."""

    id: str
    generator: str
    dataset_name: str
    status: PromptGenerationStatus
    pause_for_review: bool
    group_id: str | None
    group_label: str | None
    progress: PromptGenerationProgress
    error: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class PromptGenerationDetail(PromptGenerationSummary):
    """A generation with its request, targets, and result summary."""

    request: dict[str, JsonValue]
    targets: dict[str, str]
    review_completed: bool
    summary: dict[str, JsonValue] | None
    started_at: datetime | None


class PromptGenerationListResponse(BaseModel):
    """Generations, newest first."""

    items: list[PromptGenerationSummary]


class PromptGenerationCreateRequest(BaseModel):
    """A generation to queue."""

    model_config = ConfigDict(extra="forbid")

    generator: GeneratorNameStr
    dataset_name: DatasetNameStr = Field(..., description="Name of the dataset to create; it must not exist yet")
    request: dict[str, Any]
    targets: dict[TargetRoleStr, IdentifierStr] = Field(default_factory=dict)
    pause_for_review: bool = Field(False, description="Pause after a reviewable stage so its output can be edited")


class PromptGenerationPreflightResponse(BaseModel):
    """Whether a request can start, and an estimate of its work. No target is called."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    dataset_name_available: bool
    estimate: PromptGenerationEstimate | None = None


class PromptGenerationReview(BaseModel):
    """Output awaiting review, in the generator's review shape."""

    generation_id: str
    stage: str
    payload: dict[str, JsonValue]


class PromptGenerationReviewUpdate(BaseModel):
    """Edited review output."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


class PromptGenerationBatchItem(BaseModel):
    """One generation within a batch."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: DatasetNameStr
    request: dict[str, Any]


class PromptGenerationBatchCreateRequest(BaseModel):
    """Several generations that share a generator, targets, and settings, such as an ASSERT suite."""

    model_config = ConfigDict(extra="forbid")

    generator: GeneratorNameStr
    label: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    items: list[PromptGenerationBatchItem] = Field(..., min_length=1, max_length=100)
    targets: dict[TargetRoleStr, IdentifierStr] = Field(default_factory=dict)
    pause_for_review: bool = False


class PromptGenerationRequestFromDataset(BaseModel):
    """A generator request reconstructed from a stored dataset, to regenerate it under a new name."""

    generator: str
    request: dict[str, JsonValue]
    source_dataset_name: str
