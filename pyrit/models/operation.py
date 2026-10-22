# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


def operation_name_key(name: str) -> str:
    """
    Return the identity key that makes names unique regardless of case and surrounding whitespace.

    Returns:
        str: The trimmed, case-folded name.
    """
    return name.strip().casefold()


def _normalize_operation_name(value: str) -> str:
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


OperationName = Annotated[str, AfterValidator(_normalize_operation_name)]


class OperationCreate(BaseModel):
    """A named engagement that groups human findings."""

    model_config = ConfigDict(extra="forbid")

    name: OperationName
    description: str = ""


class Operation(OperationCreate):
    """A saved operation with stable identity and creation time."""

    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class OperationUsage(BaseModel):
    """What still refers to an Operation by ID. Activity attributed only by a deprecated name is not counted."""

    findings: int = Field(ge=0)
    attacks: int = Field(ge=0)
    scenario_runs: int = Field(ge=0)
