# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pyrit.models.literals import ChatMessageRole, PromptDataType, SeedType
from pyrit.models.score.condition import ConditionTuple
from pyrit.models.seeds.seed_origin import SeedOrigin
from pyrit.models.target.json_schema_definition import JsonSchemaDefinition


class SeedRecord(BaseModel):
    """Stored seed data for inspection, without rendering, file loading, or generated defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    seed_type: SeedType
    value: str
    value_sha256: str | None
    data_type: PromptDataType
    name: str | None
    dataset_name: str | None
    origin: SeedOrigin
    harm_categories: list[str] | None
    description: str | None
    authors: list[str] | None
    groups: list[str] | None
    source: str | None
    date_added: datetime
    added_by: str
    metadata: dict[str, Any] | None
    prompt_group_id: UUID | None
    sequence: int | None
    role: ChatMessageRole | None
    parameters: list[str] | None
    conditions: ConditionTuple | None
    response_json_schema: JsonSchemaDefinition | None
