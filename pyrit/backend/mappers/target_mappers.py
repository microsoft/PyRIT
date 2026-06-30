# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target mappers – domain → DTO translation for target-related models.

Identity vs. presentation: ``TargetIdentifier``
is the typed, lossless *identity* projection of a target's
``ComponentIdentifier``. ``TargetInstance`` is the backend *presentation* view —
it adds registry binding (``target_registry_name``), flattened capabilities, and
composite ``inner_targets`` for the frontend. These mappers read typed fields off
``TargetIdentifier`` instead of poking ``identifier.params`` by string key.
"""

from pyrit.models import TargetIdentifier
from pyrit.models.catalog.target import TargetCapabilitiesInfo, TargetInstance
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName, TargetCapabilities
from pyrit.prompt_target.round_robin_target import RoundRobinTarget

# Capability flag names that should never be surfaced as identifier-level params:
# they are sourced from `target_obj.capabilities` instead.
_CAPABILITY_PARAM_NAMES = frozenset(cap.value for cap in CapabilityName)


def _target_capabilities_to_info(
    capabilities: TargetCapabilities,
) -> TargetCapabilitiesInfo:
    """
    Build a TargetCapabilitiesInfo DTO from a domain TargetCapabilities object.

    Modality combinations are flattened into sorted unique modality lists since
    the frontend uses them only for per-piece modality checks.

    Args:
        capabilities: The domain TargetCapabilities object.

    Returns:
        TargetCapabilitiesInfo DTO mirroring the capability flags and flattened
        input/output modalities.
    """
    return TargetCapabilitiesInfo(
        supports_multi_turn=capabilities.supports_multi_turn,
        supports_multi_message_pieces=capabilities.supports_multi_message_pieces,
        supports_json_schema=capabilities.supports_json_schema,
        supports_json_output=capabilities.supports_json_output,
        supports_editable_history=capabilities.supports_editable_history,
        supports_system_prompt=capabilities.supports_system_prompt,
        supports_streaming_audio=capabilities.supports_streaming_audio,
        supported_input_modalities=sorted({str(t) for combo in capabilities.input_modalities for t in combo}),
        supported_output_modalities=sorted({str(t) for combo in capabilities.output_modalities for t in combo}),
    )


def target_object_to_instance(target_registry_name: str, target_obj: PromptTarget) -> TargetInstance:
    """
    Build a TargetInstance DTO from a registry target object.

    Extracts only the frontend-relevant fields from the internal identifier,
    avoiding leakage of internal PyRIT core structures.

    Args:
        target_registry_name: The human-friendly target registry name.
        target_obj: The domain PromptTarget object from the registry.

    Returns:
        TargetInstance DTO with metadata derived from the object.
    """
    target_identifier = TargetIdentifier.from_component_identifier(target_obj.get_identifier())

    # Promoted params (endpoint, model_name, …) are mirrored into params and also
    # exposed as typed fields; strip them so they don't leak into
    # target_specific_params. Capabilities are no longer part of the identifier at
    # all. The strip set is also defensive: it drops the explicit
    # target_specific_params bag (merged in separately) plus any legacy capability /
    # configuration keys that might appear in older persisted identifiers.
    extracted_keys = (
        {
            "target_specific_params",
            "target_configuration",
        }
        | _CAPABILITY_PARAM_NAMES
        | set(TargetIdentifier._promoted_param_fields())
    )

    # Collect remaining params as target_specific_params so the frontend can display them
    raw_specific = target_identifier.params.get("target_specific_params")
    explicit_specific = raw_specific if isinstance(raw_specific, dict) else {}
    extra = {k: v for k, v in target_identifier.params.items() if k not in extracted_keys and v is not None}
    combined_specific = {**extra, **explicit_specific} or None

    inner_targets = _build_inner_targets(target_obj)

    # For composite targets (RoundRobinTarget), hoist model_name from inner targets
    # only when ALL inner targets share the same deployment name. When they differ
    # (e.g. "gpt-4o-japan-nilfilter" vs "pyrit-github-gpt4"), show "—" for
    # consistency with how other targets display model_name.
    model_name = target_identifier.model_name or None
    underlying_model_name = target_identifier.underlying_model_name or None
    if model_name is None and inner_targets:
        inner_models = {t.model_name for t in inner_targets}
        model_name = inner_models.pop() if len(inner_models) == 1 else None
    if underlying_model_name is None and inner_targets:
        inner_underlying = {t.underlying_model_name for t in inner_targets}
        underlying_model_name = inner_underlying.pop() if len(inner_underlying) == 1 else None

    return TargetInstance(
        target_registry_name=target_registry_name,
        target_type=target_identifier.class_name,
        endpoint=target_identifier.endpoint or None,
        model_name=model_name,
        underlying_model_name=underlying_model_name,
        temperature=target_identifier.temperature,
        top_p=target_identifier.top_p,
        max_requests_per_minute=target_identifier.max_requests_per_minute,
        capabilities=_target_capabilities_to_info(target_obj.capabilities),
        target_specific_params=combined_specific,
        inner_targets=inner_targets,
        identifier_hash=target_identifier.hash,
    )


def _build_inner_targets(target_obj: PromptTarget) -> list[TargetInstance] | None:
    """
    Build inner target DTOs for composite targets like RoundRobinTarget.

    For non-composite targets, returns None. For RoundRobinTarget, recursively
    maps each inner target into a TargetInstance using the inner target's
    ``unique_name`` as the registry name (inner targets may not be independently
    registered in the registry, so we derive the name from the identifier).

    Args:
        target_obj: The domain PromptTarget object.

    Returns:
        A list of inner TargetInstance DTOs, or None for non-composite targets.
    """
    if not isinstance(target_obj, RoundRobinTarget):
        return None

    inner_instances: list[TargetInstance] = []
    for inner_target in target_obj._targets:
        inner_name = inner_target.get_identifier().unique_name
        inner_instances.append(target_object_to_instance(inner_name, inner_target))
    return inner_instances
