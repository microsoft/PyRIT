# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


def operation_name_key(name: str) -> str:
    """
    Return the identity key that makes names unique regardless of case and surrounding whitespace.

    Returns:
        str: The trimmed, case-folded name.
    """
    return name.strip().casefold()


class OperationCreate(BaseModel):
    """A named engagement that groups human findings."""

    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        """
        Trim surrounding whitespace and enforce the SQL Server NVARCHAR(128) bound.

        Returns:
            str: The trimmed display name.

        Raises:
            ValueError: If the trimmed name is blank or exceeds 128 UTF-16 code units.
        """
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank.")
        if len(value.encode("utf-16-le")) // 2 > 128:
            raise ValueError("Must be at most 128 UTF-16 code units.")
        return value


class Operation(OperationCreate):
    """A saved operation with stable identity and creation time."""

    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
