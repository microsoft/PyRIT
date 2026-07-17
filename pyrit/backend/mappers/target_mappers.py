# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target mappers – domain → DTO translation for target-related models.

Identity vs. presentation: ``TargetIdentifier`` is the typed, lossless *identity*
projection of a target's ``ComponentIdentifier`` and is embedded directly on the
``TargetInstance`` DTO. ``TargetInstance`` adds only the presentation concerns the
identifier does not own: registry binding (``target_registry_name``), capabilities,
composite ``inner_targets`` (full instances, not bare identifiers), and a curated
``target_specific_params`` view of the non-promoted constructor params.
"""

from typing import Any

from pyrit.models import TargetIdentifier
from pyrit.models.catalog.target import TargetInstance
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName

# Capability flag names that should never be surfaced as identifier-level params:
# they are sourced from `target_obj.capabilities` instead.
_CAPABILITY_PARAM_NAMES = frozenset(cap.value for cap in CapabilityName)


def _target_specific_params(target_identifier: TargetIdentifier) -> dict[str, Any] | None:
    """
    Collect the non-promoted constructor params for display.

    Promoted params (endpoint, model_name, …) are typed fields on the identifier;
    capabilities are sourced separately. Everything else is surfaced here so the
    frontend can show target-specific configuration (e.g., RoundRobin weights).

    Args:
        target_identifier: The typed identifier projection of the target.

    Returns:
        A dict of curated target-specific params, or None when there are none.
    """
    extracted_keys = (
        {
            "target_specific_params",
            "target_configuration",
        }
        | _CAPABILITY_PARAM_NAMES
        | set(TargetIdentifier._promoted_param_fields())
    )

    raw_specific = target_identifier.params.get("target_specific_params")
    explicit_specific = raw_specific if isinstance(raw_specific, dict) else {}
    extra = {k: v for k, v in target_identifier.params.items() if k not in extracted_keys and v is not None}
    return {**extra, **explicit_specific} or None


def target_object_to_instance(target_registry_name: str, target_obj: PromptTarget) -> TargetInstance:
    """
    Build a TargetInstance DTO from a registry target object.

    Identity is carried by the embedded ``identifier``; only presentation concerns
    (registry name, capabilities, inner targets, curated specific params) are
    derived here.

    Args:
        target_registry_name: The human-friendly target registry name.
        target_obj: The domain PromptTarget object from the registry.

    Returns:
        TargetInstance DTO with metadata derived from the object.
    """
    target_identifier = TargetIdentifier.from_component_identifier(target_obj.get_identifier())

    return TargetInstance(
        target_registry_name=target_registry_name,
        identifier=target_identifier,
        capabilities=target_obj.capabilities,
        target_specific_params=_target_specific_params(target_identifier),
        inner_targets=_build_inner_targets(target_obj),
    )


def _build_inner_targets(target_obj: PromptTarget) -> list[TargetInstance] | None:
    """
    Build inner target DTOs for composite targets like RoundRobinTarget.

    Composite targets expose their children via a public ``inner_targets``
    property; non-composite targets don't define one. The mapper stays generic —
    it asks the target for its inner targets rather than knowing any concrete
    composite type or reaching into private state — and recurses, deriving each
    inner registry name from the inner target's ``unique_name`` (inner targets
    may not be independently registered).

    Args:
        target_obj: The domain PromptTarget object.

    Returns:
        A list of inner TargetInstance DTOs, or None for non-composite targets.
    """
    inner_targets = getattr(target_obj, "inner_targets", None)
    if not inner_targets:
        return None
    return [target_object_to_instance(inner.get_identifier().unique_name, inner) for inner in inner_targets]
