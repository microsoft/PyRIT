# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models import Finding, FindingEvidence, Operation
from pyrit.models.harm_category import HarmCategory


class FindingOptionsResponse(BaseModel):
    """Canonical harm categories for the fixed finding form."""

    harm_types: list[HarmCategory]


class OperationListResponse(BaseModel):
    """All operations available as homes for findings."""

    items: list[Operation]


class FindingListItem(Finding):
    """A finding with a bounded bulk-derived association count."""

    evidence_count: int = Field(ge=0)


class FindingListResponse(BaseModel):
    """A bounded page of findings within one operation."""

    items: list[FindingListItem]
    has_more: bool
    next_offset: int | None


class FindingEvidenceCreateRequest(BaseModel):
    """The persisted viewer identity to attach."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: UUID
    conversation_id: str = Field(min_length=1, max_length=128)


class FindingEvidenceAttachResponse(BaseModel):
    """An attachment and whether this request created it."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    created: bool


class EvidenceAvailability(str, Enum):
    """Current viewer addressability of the live reference."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class FindingEvidenceItem(BaseModel):
    """An association with read-time source and scanner context."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    availability: EvidenceAvailability
    scenario_result_id: UUID | None


class FindingEvidenceListResponse(BaseModel):
    """A bounded page of live evidence references."""

    model_config = ConfigDict(extra="forbid")

    items: list[FindingEvidenceItem]
    has_more: bool
    next_offset: int | None
