# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target mappers – domain → DTO translation for target-related models.
"""

from pyrit.backend.models.targets import TargetInstance
from pyrit.prompt_target import PromptTarget


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
    identifier = target_obj.get_identifier()
    params = identifier.params

    # Keys that are extracted as top-level TargetInstance fields
    extracted_keys = {
        "endpoint",
        "model_name",
        "underlying_model_name",
        "temperature",
        "top_p",
        "max_requests_per_minute",
        "supports_multi_turn",
        "target_specific_params",
    }

    # Collect remaining params as target_specific_params so the frontend can display them
    explicit_specific = params.get("target_specific_params") or {}
    extra = {k: v for k, v in params.items() if k not in extracted_keys and v is not None}
    combined_specific = {**extra, **explicit_specific} or None

    return TargetInstance(
        target_registry_name=target_registry_name,
        target_type=identifier.class_name,
        endpoint=params.get("endpoint") or None,
        model_name=params.get("model_name") or None,
        underlying_model_name=params.get("underlying_model_name") or None,
        temperature=params.get("temperature"),
        top_p=params.get("top_p"),
        max_requests_per_minute=params.get("max_requests_per_minute"),
        supports_multi_turn=target_obj.capabilities.supports_multi_turn,
        target_specific_params=combined_specific,
    )
