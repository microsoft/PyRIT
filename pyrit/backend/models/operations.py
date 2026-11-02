# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from pyrit.backend.models.attacks import ScoreView
from pyrit.models import (
    AttackOutcome,
    Finding,
    FindingCreate,
    FindingEvidence,
    FindingSeverity,
    Operation,
    OperationUsage,
)
from pyrit.models.harm_category import HarmCategory
from pyrit.models.operation import OperationName


class FindingOptionsResponse(BaseModel):
    """Harm categories a finding can use."""

    harm_types: list[HarmCategory]


class OperationListItem(Operation):
    """An operation with its finding counts by severity. Severities with no findings are omitted."""

    finding_counts: dict[FindingSeverity, int]


class OperationListResponse(BaseModel):
    """All saved operations."""

    items: list[OperationListItem]


class OperationDetail(Operation):
    """An operation with what refers to it by ID. Only an operation with no usage can be deleted."""

    usage: OperationUsage


class OperationUpdateRequest(BaseModel):
    """The editable fields of an operation. Omitted fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    name: OperationName | None = None
    description: str | None = None


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


class FindingExportFormat(str, Enum):
    """A file format for exported findings."""

    DOCX = "docx"
    MARKDOWN = "markdown"


class FindingExportRequest(BaseModel):
    """The findings to download, the file format, and the dashboard origin their evidence links open."""

    model_config = ConfigDict(extra="forbid")

    finding_ids: list[UUID] = Field(min_length=1, description="Distinct findings of the operation to include")
    format: FindingExportFormat = FindingExportFormat.DOCX
    dashboard_origin: AnyHttpUrl = Field(description="The dashboard's origin, which evidence links open")

    @field_validator("finding_ids")
    @classmethod
    def _reject_duplicates(cls, finding_ids: list[UUID]) -> list[UUID]:
        """
        Require each finding to be selected once.

        Returns:
            list[UUID]: The selected finding IDs.

        Raises:
            ValueError: If a finding is selected more than once.
        """
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("Each finding can be selected only once.")
        return finding_ids

    @field_validator("dashboard_origin")
    @classmethod
    def _require_origin(cls, url: AnyHttpUrl) -> AnyHttpUrl:
        """
        Accept only a scheme, host, and optional port.

        Returns:
            AnyHttpUrl: The dashboard origin.

        Raises:
            ValueError: If the address has credentials, a path, a query, or a fragment.
        """
        if (
            url.username
            or url.password
            or url.path not in (None, "/")
            or url.query is not None
            or url.fragment is not None
        ):
            raise ValueError("Must be an origin, without credentials, a path, a query, or a fragment.")
        return url
