# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and views for prompt generation: running generator requests as background jobs,
reviewing intermediate output, and saving the result as a named seed dataset.

The stored job, its status, and its progress are ``pyrit.models`` types. Each generator defines the
shape of its ``request``, its review payload, and its result summary; ``GET /prompt-generators``
describes the registered generators.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from pyrit.backend.models.common import IdentifierStr
from pyrit.models import (
    PromptGenerationEstimate,
    PromptGenerationProgress,
    PromptGenerationStatus,
    TargetObjectiveGenerationRequest,
)
from pyrit.models.prompt_generation import DatasetNameStr, GeneratorNameStr, TargetRoleStr


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


class TargetObjectiveCreateRequest(TargetObjectiveGenerationRequest):
    """A ``target_objective`` request within the backend's size limits."""

    count: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    harm_categories: list[Annotated[str, StringConstraints(pattern=r"\S", max_length=100)]] = Field(
        default_factory=list, max_length=50
    )


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
