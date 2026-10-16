# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pyrit.backend.models.attacks import ScoreView
from pyrit.models import AttackOutcome, Finding, FindingCreate, FindingEvidence, FindingSeverity, Operation
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


class OperationUpdateRequest(BaseModel):
    """The editable fields of an operation. The name cannot be changed here."""

    model_config = ConfigDict(extra="forbid")

    description: str


class FindingListItem(Finding):
    """A finding with its evidence count."""

    evidence_count: int = Field(ge=0)


class FindingListResponse(BaseModel):
    """One page of an operation's findings."""

    items: list[FindingListItem]
    has_more: bool
    next_offset: int | None


class FindingCreateRequest(FindingCreate):
    """
    A finding to record, optionally with one saved attack result as evidence.

    Sending the same ``id`` and content again returns the saved finding, so a client can retry safely.
    """

    id: UUID | None = None
    attack_result_id: UUID | None = Field(None, description="A saved attack result to link as evidence")


class FindingEvidenceCreateRequest(BaseModel):
    """The saved attack result to link."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: UUID


class FindingEvidenceAttachResponse(BaseModel):
    """Linked evidence and whether this request created the link."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    created: bool


class EvidenceAvailability(str, Enum):
    """Whether a linked attack result can still be opened."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class FindingEvidenceItem(BaseModel):
    """Linked evidence with its attack result's availability, objective, outcome, score, and source Operation."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    availability: EvidenceAvailability
    scenario_result_id: UUID | None
    source_operation_id: UUID | None = Field(
        None, description="The attack's own saved Operation, which may differ from the finding's"
    )
    source_operation_name: str | None = Field(
        None, description="Current name of the attack's Operation, or the historical name for legacy activity"
    )
    objective: str | None = Field(None, description="The attack's objective")
    outcome: AttackOutcome | None = Field(None, description="The attack's current outcome")
    score: ScoreView | None = Field(
        None, description="The attack's current score: its human score, otherwise its automated score"
    )
    conversation_id: str | None = Field(
        None, description="The conversation to open: the one holding the score, otherwise the main conversation"
    )


class FindingEvidenceListResponse(BaseModel):
    """One page of a finding's evidence."""

    model_config = ConfigDict(extra="forbid")

    items: list[FindingEvidenceItem]
    has_more: bool
    next_offset: int | None
