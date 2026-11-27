# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import StrEnum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, computed_field, field_validator

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.common import MAX_ITEMS, IdentifierStr, TextStr
from pyrit.models import (
    AttackOutcome,
    DecompositionPath,
    Finding,
    FindingCreate,
    FindingEvidence,
    Operation,
    OperationCreate,
    OperationUsage,
)
from pyrit.models.finding_fields import FindingValueRow
from pyrit.models.harm_category import HarmCategory
from pyrit.models.operation import OperationName

# Ten pages of the export dialog; the dashboard keeps its selection within this.
MAX_EXPORT_FINDINGS = 1_000


class OperationListItem(Operation):
    """An operation with its finding counts by stored severity value. Severities with no findings are omitted."""

    finding_counts: dict[str, int]


class OperationListResponse(BaseModel):
    """All saved operations."""

    items: list[OperationListItem]


class OperationDetail(Operation):
    """An operation with what refers to it by ID. Only an operation with no usage can be deleted."""

    usage: OperationUsage


class OperationCreateRequest(OperationCreate):
    """An operation to create."""

    description: TextStr = ""


class OperationUpdateRequest(BaseModel):
    """The editable fields of an operation. Omitted fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    name: OperationName | None = None
    description: TextStr | None = None


class FindingPathItem(DecompositionPath):
    """A path of a finding, with whether it is complete."""

    complete: bool = Field(description="Whether every step is a demonstrated sub-finding. Derived, never stored.")


# TODO(PyRIT 1.5): Remove with GET /operations/finding-options.
class FindingOptionsResponse(BaseModel):
    """The harm categories a finding could use in PyRIT 1.2."""

    harm_types: list[HarmCategory]


class FindingResponse(Finding):
    """A saved finding, with the PyRIT 1.2 fields that its values stand for."""

    # TODO(PyRIT 1.5): Remove the PyRIT 1.2 fields.
    @computed_field(json_schema_extra={"deprecated": True})
    @property
    def description(self) -> str:
        """Deprecated: read field_values["description"] instead, which is absent when not set."""
        return self._deprecated_values().description

    @computed_field(json_schema_extra={"deprecated": True})
    @property
    def severity_other(self) -> str | None:
        """Deprecated: read severity, which holds Other text itself."""
        return self._deprecated_values().severity_other

    @computed_field(json_schema_extra={"deprecated": True})
    @property
    def harm_type(self) -> HarmCategory | None:
        """Deprecated: read field_values["harm_type"] instead, which is absent when not set."""
        return self._deprecated_values().harm_type

    @computed_field(json_schema_extra={"deprecated": True})
    @property
    def harm_type_other(self) -> str | None:
        """Deprecated: read field_values["harm_type"] instead, which is absent when not set."""
        return self._deprecated_values().harm_type_other


class FindingListItem(FindingResponse):
    """
    A finding with its evidence and sub-finding counts, whether each of its paths is complete, and its values as the
    active finding fields label them.
    """

    evidence_count: int = Field(ge=0)
    child_count: int = Field(ge=0, description="The number of direct sub-findings")
    # A response narrows each path to include its derived completeness; list items are never reassigned.
    paths: tuple[FindingPathItem, ...] = ()  # type: ignore[ty:incompatible-mutable-override]
    rows: list[FindingValueRow] = Field(description="The filled values in configured order, labelled for display")
    retired: list[tuple[str, str]] = Field(
        description="The (key, value) of each stored value that no active field or level uses, in stored order"
    )


class AttackFindingItem(Finding):
    """A finding that links an attack as evidence, with its Operation's current name and the evidence link."""

    operation_name: str
    evidence_id: UUID = Field(description="The evidence link, which unlinks the attack from the finding")


class AttackFindingListResponse(BaseModel):
    """Every finding that links an attack, highest severity first, then newest first."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: UUID
    items: list[AttackFindingItem]


class FindingListResponse(BaseModel):
    """One page of an operation's findings."""

    items: list[FindingListItem]
    has_more: bool
    next_offset: int | None


class _FindingRequestFields(FindingCreate):
    """A finding's values as a request carries them, within the request limits."""

    title: TextStr
    severity: TextStr = Field(
        description="The key of a severity option, or Other text; surrounding whitespace is removed"
    )
    field_values: dict[IdentifierStr, TextStr] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description="Values by field or level key, each an option's key or text; blank values are dropped",
    )


class FindingCreateRequest(_FindingRequestFields):
    """
    A finding to record, optionally under a parent finding and with one saved attack result as evidence.

    Sending the same ``id`` and content again returns the saved finding, as long as it still fits the active finding
    fields, so a client can retry safely.
    """

    id: UUID | None = None
    attack_result_id: UUID | None = Field(None, description="A saved attack result to link as evidence")


class FindingUpdateRequest(_FindingRequestFields):
    """
    A finding's new editable values, replacing the saved ones, and the retired values to keep or remove.

    An omitted ``parent_finding_id`` makes the finding top-level. A finding moves with its sub-findings, and paths
    are replaced separately.
    """

    keep_retired_keys: list[IdentifierStr] = Field(
        default_factory=list,
        max_length=MAX_ITEMS,
        description="Keys of stored values to keep unchanged, even if the active fields use them again",
    )
    remove_retired_keys: list[IdentifierStr] = Field(
        default_factory=list, max_length=MAX_ITEMS, description="Keys of the retired values to delete from the finding"
    )


class FindingPathRequest(DecompositionPath):
    """A path to save. Omit ``id`` to add a new path."""

    name: TextStr | None = None
    description: TextStr = ""
    steps: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_ITEMS, description="Direct sub-findings, in order")


class FindingPathsUpdateRequest(BaseModel):
    """Every path of a finding, in order, replacing the saved ones. An empty list removes them all."""

    model_config = ConfigDict(extra="forbid")

    paths: list[FindingPathRequest] = Field(max_length=MAX_ITEMS)

    @field_validator("paths")
    @classmethod
    def _reject_repeated_ids(cls, paths: list[FindingPathRequest]) -> list[FindingPathRequest]:
        """
        Require each path to appear once.

        Returns:
            list[FindingPathRequest]: The paths.

        Raises:
            ValueError: If a path ID repeats.
        """
        if len({path.id for path in paths}) != len(paths):
            raise ValueError("Each path can appear only once.")
        return paths


class FindingEvidenceCreateRequest(BaseModel):
    """The saved attack result to link."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: UUID


class FindingEvidenceAttachResponse(BaseModel):
    """Linked evidence and whether this request created the link."""

    model_config = ConfigDict(extra="forbid")

    item: FindingEvidence
    created: bool


class EvidenceAvailability(StrEnum):
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


class FindingExportFormat(StrEnum):
    """A file format for exported findings."""

    DOCX = "docx"
    MARKDOWN = "markdown"


class FindingExportRequest(BaseModel):
    """The findings to download, the file format, and the dashboard origin their evidence links open."""

    model_config = ConfigDict(extra="forbid")

    finding_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_EXPORT_FINDINGS,
        description="Distinct findings of the operation to include, each with all of its sub-findings",
    )
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
