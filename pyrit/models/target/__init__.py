# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

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

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
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
    from pyrit.models.target.token_usage import (
        TOKEN_USAGE_METADATA_PREFIX,
        TokenUsage,
        read_usage_int,
        read_usage_value,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "COMMON_JSON_SCHEMAS": "pyrit.models.target.json_schema_definition",
    "CapabilityName": "pyrit.models.target.target_capabilities",
    "JSON_SCHEMA_METADATA_KEY": "pyrit.models.target.json_schema_definition",
    "JsonResponseConfig": "pyrit.models.target.json_response_config",
    "JsonSchemaDefinition": "pyrit.models.target.json_schema_definition",
    "SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY": "pyrit.models.target.json_schema_definition",
    "TOKEN_USAGE_METADATA_PREFIX": "pyrit.models.target.token_usage",
    "TargetCapabilities": "pyrit.models.target.target_capabilities",
    "TokenUsage": "pyrit.models.target.token_usage",
    "get_common_json_schema": "pyrit.models.target.json_schema_definition",
    "read_usage_int": "pyrit.models.target.token_usage",
    "read_usage_value": "pyrit.models.target.token_usage",
    "register_common_json_schema": "pyrit.models.target.json_schema_definition",
    "unregister_common_json_schema": "pyrit.models.target.json_schema_definition",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public target export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
