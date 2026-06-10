# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from pyrit.models.json_schema_definition import (
    JSON_SCHEMA_METADATA_KEY,  # noqa: TC001  (runtime-required by Pydantic field annotations)
)

# Would prefer StrEnum, but.... Python 3.10
_METADATAKEYS = {
    "RESPONSE_FORMAT": "response_format",
    "JSON_SCHEMA": JSON_SCHEMA_METADATA_KEY,
    "JSON_SCHEMA_NAME": "json_schema_name",
    "JSON_SCHEMA_STRICT": "json_schema_strict",
}


class _JsonResponseConfig(BaseModel):
    """
    Configuration for JSON responses (OpenAI request-format vocabulary).

    Parses an OpenAI-shaped ``response_format`` / ``text.format`` block out of
    a ``MessagePiece.prompt_metadata`` dict. Lives in the ``pyrit.prompt_target``
    layer because the keys it speaks (``"response_format"``, ``"json_schema_name"``,
    ``"json_schema_strict"``) are OpenAI request-shape vocabulary, not
    framework-wide concepts. The framework-wide pieces (the
    ``JsonSchemaDefinition`` alias and the ``JSON_SCHEMA_METADATA_KEY`` contract)
    stay in ``pyrit.models``.

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
