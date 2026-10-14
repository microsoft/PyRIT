# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.backend.models.attacks import ScoreView
from pyrit.models import Finding, FindingCreate, FindingEvidence, FindingSeverity, Operation
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
    A finding to record, optionally with one saved conversation as evidence.

    Sending the same ``id`` and content again returns the saved finding, so a client can retry safely.
    """

    id: UUID | None = None
    conversation_id: str | None = Field(None, min_length=1, max_length=128)
    attack_result_id: UUID | None = Field(None, description="The conversation's attack; looked up when omitted")

    @model_validator(mode="after")
    def _require_conversation_for_attack(self) -> Self:
        """
        Reject an attack reference that names no conversation.

        Returns:
            Self: The validated request.

        Raises:
            ValueError: If an attack is given without a conversation.
        """
        if self.attack_result_id is not None and self.conversation_id is None:
            raise ValueError("attack_result_id requires conversation_id.")
        return self


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
    """Linked evidence with its conversation's availability, attack context, score, and source Operation."""

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
    objective: str | None = Field(None, description="The owning attack's objective")
    is_main_conversation: bool | None = Field(
        None, description="Whether the conversation is its attack's main conversation rather than a branch"
    )
    score: ScoreView | None = Field(None, description="This conversation's own score; never an auxiliary score")


class FindingEvidenceListResponse(BaseModel):
    """One page of a finding's evidence."""

    model_config = ConfigDict(extra="forbid")

    items: list[FindingEvidenceItem]
    has_more: bool
    next_offset: int | None
