# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ToolDefinition(BaseModel):
    """The advertised contract of a function tool, without an executable callback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = ""
    #: JSON Schema for the tool's arguments.
    parameters: dict[str, JsonValue]
