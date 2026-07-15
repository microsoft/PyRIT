# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from pyrit.models.target.json_schema_definition import (
    JSON_SCHEMA_METADATA_KEY,  # noqa: TC001  (runtime-required by Pydantic field annotations)
)


class JsonResponseConfig(BaseModel):
    """
    Canonical PyRIT configuration for requesting a JSON response from a target.

    A value object that owns PyRIT's canonical ``MessagePiece.prompt_metadata`` keys for
    JSON responses (``"response_format"``, ``JSON_SCHEMA_METADATA_KEY``, ``"json_schema_name"``,
    ``"json_schema_strict"``) and (de)serializes them via ``from_metadata`` / ``to_metadata``.
    Producers (scorers, attacks, converters) build one and call ``to_metadata`` to attach it to
    a piece; targets read it back with ``from_metadata``.

    Providing a ``json_schema`` implies ``enabled`` (a schema is meaningless without JSON output),
    so JSON-object mode is ``enabled=True`` with no schema and JSON-schema mode is just
    ``json_schema=...``.

    These are PyRIT keys, not a provider's wire format. Translating this config into a specific
    provider's request block (e.g. the OpenAI chat ``response_format`` or Responses ``text.format``
    shape) is the target's job and lives in ``pyrit.prompt_target`` (``build_response_format`` /
    ``_build_text_format``).

    For the provider shapes those translators emit, see:
    https://platform.openai.com/docs/api-reference/chat/create#chat_create-response_format-json_schema
    and
    https://platform.openai.com/docs/api-reference/responses/create#responses_create-text
    """

    model_config = ConfigDict(extra="forbid")

    _METADATAKEYS: ClassVar[dict[str, str]] = {
        "RESPONSE_FORMAT": "response_format",
        "JSON_SCHEMA": JSON_SCHEMA_METADATA_KEY,
        "JSON_SCHEMA_NAME": "json_schema_name",
        "JSON_SCHEMA_STRICT": "json_schema_strict",
    }

    enabled: bool = False
    json_schema: dict[str, Any] | None = None
    schema_name: str = "CustomSchema"
    strict: bool = True

    @model_validator(mode="after")
    def _schema_implies_enabled(self) -> JsonResponseConfig:
        if self.json_schema is not None:
            self.enabled = True
        return self

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> JsonResponseConfig:
        """
        Reconstruct a config from a ``MessagePiece``'s ``prompt_metadata``.

        Reads the canonical ``response_format`` / ``json_schema`` keys written by ``to_metadata``.
        Returns a disabled config when JSON output was not requested (``response_format`` absent or
        not ``"json"``). A schema stored as a JSON string is parsed back into a dict.

        Args:
            metadata (dict[str, Any] | None): The prompt metadata to read.

        Returns:
            JsonResponseConfig: The reconstructed config (``enabled=False`` when none requested).

        Raises:
            ValueError: If a schema string is present but is not valid JSON.
        """
        if not metadata:
            return cls(enabled=False)

        response_format = metadata.get(cls._METADATAKEYS["RESPONSE_FORMAT"])
        if response_format != "json":
            return cls(enabled=False)

        schema_val = metadata.get(cls._METADATAKEYS["JSON_SCHEMA"])
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
                schema_name=metadata.get(cls._METADATAKEYS["JSON_SCHEMA_NAME"], "CustomSchema"),
                strict=metadata.get(cls._METADATAKEYS["JSON_SCHEMA_STRICT"], True),
            )

        return cls(enabled=True)

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize to the canonical ``response_format`` / ``json_schema`` metadata keys.

        Symmetric with ``from_metadata``: a disabled config produces an empty dict
        (nothing to request), an enabled config without a schema produces just the
        ``response_format`` marker, and an enabled config with a schema also writes the
        schema body plus its name and strict flag. The result is meant to be merged into
        a ``MessagePiece.prompt_metadata`` dict.

        Returns:
            dict[str, Any]: The metadata fragment to attach to a piece.
        """
        if not self.enabled:
            return {}

        metadata: dict[str, Any] = {self._METADATAKEYS["RESPONSE_FORMAT"]: "json"}
        if self.json_schema is not None:
            metadata[self._METADATAKEYS["JSON_SCHEMA"]] = self.json_schema
            metadata[self._METADATAKEYS["JSON_SCHEMA_NAME"]] = self.schema_name
            metadata[self._METADATAKEYS["JSON_SCHEMA_STRICT"]] = self.strict
        return metadata
