# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

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
    """A finding as an operator enters it, independent of attack outcomes and scores."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    severity: FindingSeverity
    severity_other: str | None = None
    harm_type: HarmCategory | None = None
    harm_type_other: str | None = None

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
    """A saved finding in one operation."""

    operation_id: UUID
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class FindingEvidence(BaseModel):
    """A conversation linked to a finding as evidence."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    finding_id: UUID
    conversation_id: str = Field(min_length=1, max_length=128)
    attack_result_id: UUID
    attached_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
