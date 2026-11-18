# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.models.decomposition import DecompositionNode, DecompositionPath, validate_own_decomposition


class FindingCreate(BaseModel):
    """
    A finding as an operator enters it, independent of attack outcomes and scores.

    A sub-finding names its parent finding. Every finding keeps its own severity, and ``demonstrated`` records
    whether the operator showed it, so a decomposition can include steps that were not achieved.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    severity: str = Field(description="The key of a severity option, or Other text; surrounding whitespace is removed")
    field_values: dict[str, str] = Field(
        {}, description="Values by field or level key, each an option's key or text; blank values are dropped"
    )
    parent_finding_id: UUID | None = None
    demonstrated: bool = True

    @field_validator("title")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """
        Reject blank text without normalizing its contents.

        Returns:
            str: The original value.

        Raises:
            ValueError: If the value is blank.
        """
        if not value.strip():
            raise ValueError("Must not be blank.")
        return value

    @field_validator("severity")
    @classmethod
    def _strip_severity(cls, value: str) -> str:
        """
        Remove surrounding whitespace and reject blank text.

        Returns:
            str: The severity without surrounding whitespace.

        Raises:
            ValueError: If the severity is blank.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("Must not be blank.")
        return stripped

    @field_validator("field_values")
    @classmethod
    def _drop_blank_values(cls, value: dict[str, str]) -> dict[str, str]:
        """
        Drop empty and whitespace-only values, keeping other text exactly as entered.

        Returns:
            dict[str, str]: The non-blank values.
        """
        return {key: text for key, text in value.items() if text.strip()}


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
