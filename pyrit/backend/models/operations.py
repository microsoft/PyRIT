# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models import Finding, FindingEvidence, FindingSeverity, Operation
from pyrit.models.harm_category import HarmCategory


class FindingOptionsResponse(BaseModel):
    """Harm categories a finding can use."""

    harm_types: list[HarmCategory]


class OperationListItem(Operation):
    """An operation with its finding counts by severity. Severities with no findings are omitted."""

    finding_counts: dict[FindingSeverity, int]


class OperationListResponse(BaseModel):
    """All saved operations."""

    items: list[OperationListItem]


class FindingListItem(Finding):
    """A finding with its evidence count."""

    evidence_count: int = Field(ge=0)


class FindingListResponse(BaseModel):
    """One page of an operation's findings."""

    items: list[FindingListItem]
    has_more: bool
    next_offset: int | None


class FindingEvidenceCreateRequest(BaseModel):
    """The saved conversation to link, identified by its attack and conversation ids."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: UUID
    conversation_id: str = Field(min_length=1, max_length=128)


class FindingEvidenceAttachResponse(BaseModel):
    """Linked evidence and whether this request created the link."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    created: bool


class EvidenceAvailability(str, Enum):
    """Whether a linked conversation can still be opened."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class FindingEvidenceItem(BaseModel):
    """Linked evidence with its conversation's current availability and scanner run, if any."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    availability: EvidenceAvailability
    scenario_result_id: UUID | None


class FindingEvidenceListResponse(BaseModel):
    """One page of a finding's evidence."""

    model_config = ConfigDict(extra="forbid")

    items: list[FindingEvidenceItem]
    has_more: bool
    next_offset: int | None
