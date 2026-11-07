# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target instance catalog models.

Targets have two concepts:

- Types: Static metadata bundled with the frontend (from the registry).
- Instances: Runtime objects created via the API with specific configuration.

The ``TargetInstance`` model is the wire-format snapshot for a runtime
target, used by both the backend (as a REST response payload) and external
REST clients (the CLI today, future external clients tomorrow). Because it
*is* the REST response model (FastAPI serves it directly), per-field
``Field(..., description=...)`` strings live here so they surface in the
generated OpenAPI schema.

Identity lives on the embedded ``TargetIdentifier`` (class
name, endpoint, model name, generation params, inner-target identifiers) and is
*not* duplicated as flat fields on ``TargetInstance``. Capabilities live on the
embedded ``TargetCapabilities``.
"""

from enum import Enum

from pydantic import BaseModel, Field

from pyrit.models.identifiers.component_identifier import JSONValue
from pyrit.models.identifiers.target_identifier import TargetIdentifier
from pyrit.models.target.target_capabilities import TargetCapabilities


class TargetOrigin(str, Enum):
    """Where a registered target comes from, and so whether it is still there after a restart."""

    SAVED = "saved"
    CONFIGURATION = "configuration"
    TEMPORARY = "temporary"


class TargetInstance(BaseModel):
    """
    A runtime target instance.

    Created either by an initializer (at startup) or by user (via API).
    Also used as the create-target response (same shape as GET).

    Identity (class name, endpoint, model name, generation params, and inner
    target identifiers) is carried by the typed ``identifier``; those values are
    read from there rather than mirrored as flat fields. ``target_registry_name``,
    ``capabilities``, ``inner_targets``, and ``target_specific_params`` are
    presentation concerns the identifier does not own.
    """

    target_registry_name: str = Field(..., description="Target registry key (e.g., 'azure_openai_chat')")
    reconstructable: bool = False
    reconstruction_error: str | None = None
    supports_temperature_override: bool = False
    identifier: TargetIdentifier = Field(
        ...,
        description=(
            "The target's typed, lossless TargetIdentifier: class name, promoted "
            "params (endpoint, model_name, temperature, ...), content hash, and inner "
            "target identifiers. Source of truth for the target's identity."
        ),
    )
    capabilities: TargetCapabilities = Field(..., description="Structured snapshot of target capabilities")
    supports_history_replay: bool = Field(
        False,
        description="Effective history replay support, including configured adapters and provider restoration",
    )
    target_specific_params: dict[str, JSONValue] | None = Field(
        None,
        description="Non-promoted constructor parameters, curated for display (e.g., RoundRobin weights)",
    )
    inner_targets: list["TargetInstance"] | None = Field(
        None,
        description="Inner targets for composite targets like RoundRobinTarget (full instances, not just identifiers)",
    )
    origin: TargetOrigin | None = Field(
        None,
        description=(
            "Where the target comes from: 'saved' (restored at every start), 'configuration' (the server's "
            "startup scripts), or 'temporary' (created through the API without saving, so gone after a "
            "restart). None for an inner target"
        ),
    )
