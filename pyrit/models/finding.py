# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.models.decomposition import DecompositionNode, DecompositionPath, validate_own_decomposition
from pyrit.models.harm_category import HarmCategory


class FindingSeverity(str, Enum):
    """Operator-selected severity, ordered from highest to lowest."""

    CRITICAL = "critical"
    IMPORTANT = "important"
    MODERATE = "moderate"
    LOW = "low"
    INFORMATIONAL = "informational"
    OTHER = "other"


class FindingCreate(BaseModel):
    """
    A finding as an operator enters it, independent of attack outcomes and scores.

    A sub-finding names its parent finding. Every finding keeps its own severity, and ``demonstrated`` records
    whether the operator showed it, so a decomposition can include steps that were not achieved.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    severity: FindingSeverity
    severity_other: str | None = None
    harm_type: HarmCategory | None = None
    harm_type_other: str | None = None
    parent_finding_id: UUID | None = None
    demonstrated: bool = True

    @model_validator(mode="after")
    def _validate_classifications(self) -> Self:
        """
        Require custom text only for an Other selection.

        Returns:
            Self: The validated finding.

        Raises:
            ValueError: If custom text is missing, blank, or has no Other selection.
        """
        for field, is_other, text in [
            ("severity", self.severity == FindingSeverity.OTHER, self.severity_other),
            ("harm_type", self.harm_type == HarmCategory.OTHER, self.harm_type_other),
        ]:
            if is_other:
                if text is None or not text.strip():
                    raise ValueError(f"{field}_other must not be blank when {field} is Other.")
            elif text is not None:
                raise ValueError(f"{field}_other is only allowed when {field} is Other.")
        return self

    @field_validator("title")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """
        Reject a blank title without normalizing its contents.

        Returns:
            str: The original value.

        Raises:
            ValueError: If the value is blank.
        """
        if not value.strip():
            raise ValueError("Must not be blank.")
        return value


class Finding(FindingCreate):
    """
    A saved finding in one operation.

    Findings decompose like ``SeedObjective``s, without being linked to them: a parent lists ``paths``, each an
    ordered chain of its direct sub-findings that together demonstrate it. Paths are replaced on their own, so
    creating or updating a finding's fields never changes them.
    """

    operation_id: UUID
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    paths: tuple[DecompositionPath, ...] = ()

    @property
    def decomposition_node(self) -> DecompositionNode:
        """The finding's decomposition relationships."""
        return DecompositionNode(id=self.id, parent_id=self.parent_finding_id, paths=self.paths)

    @model_validator(mode="after")
    def _validate_decomposition(self) -> Self:
        """
        Check the relationships the finding can validate without its relatives.

        Returns:
            Self: The validated finding.
        """
        validate_own_decomposition(node_id=self.id, parent_id=self.parent_finding_id, paths=self.paths)
        return self


def is_finding_path_complete(*, path: DecompositionPath, demonstrated: Mapping[UUID, bool]) -> bool:
    """
    Whether every step of a finding's path was demonstrated. Completeness is derived, never stored.

    Args:
        path (DecompositionPath): A path of direct sub-findings.
        demonstrated (Mapping[UUID, bool]): Each known sub-finding's demonstrated flag. Unknown steps count as
            not demonstrated.

    Returns:
        bool: True if each step is a demonstrated sub-finding.
    """
    return all(demonstrated.get(step, False) for step in path.steps)


class FindingEvidence(BaseModel):
    """A saved attack result linked to a finding as evidence."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    finding_id: UUID
    attack_result_id: UUID
    attached_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
