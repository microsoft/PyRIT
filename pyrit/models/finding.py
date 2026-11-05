# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class FindingCreate(BaseModel):
    """A finding as an operator enters it, independent of attack outcomes and scores."""

    model_config = ConfigDict(extra="forbid")

    title: str
    severity: str = Field(description="The key of a severity option, or Other text; surrounding whitespace is removed")
    field_values: dict[str, str] = Field(
        {}, description="Values by field or level key, each an option's key or text; blank values are dropped"
    )

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
    """A saved finding in one operation."""

    operation_id: UUID
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class FindingEvidence(BaseModel):
    """A saved attack result linked to a finding as evidence."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    finding_id: UUID
    attack_result_id: UUID
    attached_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
