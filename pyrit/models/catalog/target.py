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

from pydantic import BaseModel, Field

from pyrit.models.identifiers.component_identifier import JSONValue
from pyrit.models.identifiers.target_identifier import TargetIdentifier
from pyrit.models.target_capabilities import TargetCapabilities


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
    identifier: TargetIdentifier = Field(
        ...,
        description=(
            "The target's typed, lossless TargetIdentifier: class name, promoted "
            "params (endpoint, model_name, temperature, ...), content hash, and inner "
            "target identifiers. Source of truth for the target's identity."
        ),
    )
    capabilities: TargetCapabilities = Field(..., description="Structured snapshot of target capabilities")
    target_specific_params: dict[str, JSONValue] | None = Field(
        None,
        description="Non-promoted constructor parameters, curated for display (e.g., RoundRobin weights)",
    )
    inner_targets: list["TargetInstance"] | None = Field(
        None,
        description="Inner targets for composite targets like RoundRobinTarget (full instances, not just identifiers)",
    )
