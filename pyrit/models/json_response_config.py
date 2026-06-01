# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

# Would prefer StrEnum, but.... Python 3.10
_METADATAKEYS = {
    "RESPONSE_FORMAT": "response_format",
    "JSON_SCHEMA": "json_schema",
    "JSON_SCHEMA_NAME": "json_schema_name",
    "JSON_SCHEMA_STRICT": "json_schema_strict",
}


class _JsonResponseConfig(BaseModel):
    """
    Configuration for JSON responses (with OpenAI).

    For more details, see:
    https://platform.openai.com/docs/api-reference/chat/create#chat_create-response_format-json_schema
    and
    https://platform.openai.com/docs/api-reference/responses/create#responses_create-text
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    json_schema: dict[str, Any] | None = None
    schema_name: str = "CustomSchema"
    strict: bool = True

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> _JsonResponseConfig:
        if not metadata:
            return cls(enabled=False)

        response_format = metadata.get(_METADATAKEYS["RESPONSE_FORMAT"])
        if response_format != "json":
            return cls(enabled=False)

        schema_val = metadata.get(_METADATAKEYS["JSON_SCHEMA"])
        if schema_val is not None:
            if isinstance(schema_val, str):
                try:
                    schema = json.loads(schema_val) if schema_val else None
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON schema provided: {schema_val}") from e
            else:
                schema = schema_val

            return cls(
                enabled=True,
                json_schema=schema,
                schema_name=metadata.get(_METADATAKEYS["JSON_SCHEMA_NAME"], "CustomSchema"),
                strict=metadata.get(_METADATAKEYS["JSON_SCHEMA_STRICT"], True),
            )

        return cls(enabled=True)
