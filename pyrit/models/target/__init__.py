# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Canonical data models for how PyRIT interacts with targets.

This sub-package groups the value objects that describe a target interaction and
own their own ``MessagePiece.prompt_metadata`` (de)serialization:

* ``TokenUsage`` — provider-agnostic token accounting for a model call.
* ``JsonResponseConfig`` — PyRIT's canonical JSON-response request config.
* ``TargetCapabilities`` / ``CapabilityName`` — what a target natively supports.
* ``JsonSchemaDefinition`` and the shared JSON-schema registry / metadata keys.

Everything here is re-exported from the top-level ``pyrit.models`` package, so
callers should keep importing from ``pyrit.models`` (e.g.
``from pyrit.models import TokenUsage``).
"""

from pyrit.models.target.json_response_config import JsonResponseConfig
from pyrit.models.target.json_schema_definition import (
    COMMON_JSON_SCHEMAS,
    JSON_SCHEMA_METADATA_KEY,
    SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY,
    JsonSchemaDefinition,
    get_common_json_schema,
    register_common_json_schema,
    unregister_common_json_schema,
)
from pyrit.models.target.target_capabilities import CapabilityName, TargetCapabilities
from pyrit.models.target.token_usage import TokenUsage

__all__ = [
    "COMMON_JSON_SCHEMAS",
    "CapabilityName",
    "JSON_SCHEMA_METADATA_KEY",
    "JsonResponseConfig",
    "JsonSchemaDefinition",
    "SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY",
    "TargetCapabilities",
    "TokenUsage",
    "get_common_json_schema",
    "register_common_json_schema",
    "unregister_common_json_schema",
]
