# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target instance models.

Targets have two concepts:
- Types: Static metadata bundled with frontend (from registry)
- Instances: Runtime objects created via API with specific configuration

This module defines the Instance models for runtime target management.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo


class TargetCapabilitiesInfo(BaseModel):
    """
    Wire-format snapshot of a target's capabilities.

    Mirrors the domain ``TargetCapabilities`` dataclass for API consumers
    (notably the GUI). Modality combinations (``frozenset[frozenset[...]]``)
    are flattened into sorted unique modality lists since the frontend uses
    them only for per-piece modality checks.
    """

    supports_multi_turn: bool = Field(False, description="Target natively supports multi-turn conversations")
    supports_multi_message_pieces: bool = Field(
        False, description="Target supports multiple message pieces in a single request"
    )
    supports_json_schema: bool = Field(False, description="Target can constrain output to a provided JSON schema")
    supports_json_output: bool = Field(False, description="Target supports JSON output mode")
    supports_editable_history: bool = Field(False, description="Target allows attack history to be modified")
    supports_system_prompt: bool = Field(False, description="Target natively supports system prompts")
    supported_input_modalities: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Sorted unique input modality data types the target accepts (e.g., ['image_path', 'text'])",
    )
    supported_output_modalities: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Sorted unique output modality data types the target produces (e.g., ['audio_path', 'text'])",
    )


class TargetInstance(BaseModel):
    """
    A runtime target instance.

    Created either by an initializer (at startup) or by user (via API).
    Also used as the create-target response (same shape as GET).
    """

    target_registry_name: str = Field(..., description="Target registry key (e.g., 'azure_openai_chat')")
    target_type: str = Field(..., description="Target class name (e.g., 'OpenAIChatTarget')")
    endpoint: str | None = Field(None, description="Target endpoint URL")
    model_name: str | None = Field(None, description="Model or deployment name used in API calls")
    underlying_model_name: str | None = Field(None, description="Underlying model name if different (e.g., 'gpt-4o')")
    temperature: float | None = Field(None, description="Temperature parameter for generation")
    top_p: float | None = Field(None, description="Top-p parameter for generation")
    max_requests_per_minute: int | None = Field(None, description="Maximum requests per minute")
    capabilities: TargetCapabilitiesInfo = Field(..., description="Structured snapshot of target capabilities")
    target_specific_params: dict[str, Any] | None = Field(None, description="Additional target-specific parameters")
    inner_targets: list["TargetInstance"] | None = Field(
        None, description="Inner targets for composite targets like RoundRobinTarget"
    )
    identifier_hash: str | None = Field(None, description="ComponentIdentifier content hash for duplicate detection")


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class ValidateCapabilitiesResponse(BaseModel):
    """
    Response from validating a target's declared capabilities against observed behavior.

    Surfaces what the target class declares versus what live probing observed,
    so users can spot drift caused by gateways stripping features, model
    deployments lacking capabilities, or misconfiguration.
    """

    target_registry_name: str = Field(..., description="Target registry key the validation ran against")
    declared: TargetCapabilitiesInfo = Field(..., description="Capabilities as declared by the target class")
    observed: TargetCapabilitiesInfo = Field(..., description="Capabilities as observed by live probing")
    # Drives the frontend "Not probed (no asset)" row beneath the input-modalities
    # row. Without this field, the engine's `queried | (declared - test_modalities)`
    # math at discover_target_capabilities.py:778 ORs non-probeable combinations
    # back into observed, making observed == declared, and the frontend has no way
    # to distinguish "genuinely confirmed" from "not probed".
    non_probeable_input_modalities: list[str] = Field(
        default_factory=list,
        description=(
            "Sorted list of declared input-modality combinations that could NOT be probed "
            "because the engine has no packaged test asset for the contained types. Each "
            "entry is a '+'-joined sorted combination (e.g., 'function_call' or 'image_path+url'). "
            "The frontend renders the union of these as a single 'Not probed (no asset)' row "
            "beneath the input-modalities row."
        ),
    )
    # Distinct from ``non_probeable_input_modalities`` (which carries the
    # combo display strings). When a target declares both a probeable combo
    # like ``{text}`` and a non-probeable mixed combo like ``{text,
    # function_call}``, splitting the combo string on '+' and stripping every
    # piece from the input-modality cells would incorrectly hide ``text`` —
    # which *was* probed and confirmed via the singleton combo. This field
    # lists only the types that never appear in any probeable combo, so the
    # frontend can safely filter cells without dropping confirmed modalities.
    non_probeable_only_types: list[str] = Field(
        default_factory=list,
        description=(
            "Sorted list of declared input modality types that appear ONLY in non-probeable "
            "combinations (never in any probeable combination). The frontend uses this set to "
            "hide truly unprobed types from the input-modality cells while leaving types that "
            "were confirmed via a probeable singleton combo visible. Disjoint from the types "
            "implicit in ``observed.supported_input_modalities`` that came from a probeable probe."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Operational notes for the user (e.g., 'this validation wrote test prompts to memory', "
            "'output modalities are not probed and fall through to declared values', "
            "'do not validate while an attack is actively running against this target')."
        ),
    )


class CreateTargetRequest(BaseModel):
    """Request to create a new target instance."""

    type: str = Field(..., description="Target type (e.g., 'OpenAIChatTarget')")
    params: dict[str, Any] = Field(default_factory=dict, description="Target constructor parameters")
    auth_mode: Literal["api_key", "entra"] = Field(
        "api_key",
        description=(
            "Authentication mode. 'api_key' uses the api_key in params (default). "
            "'entra' uses Microsoft Entra ID; requires an Azure endpoint and is "
            "supported by OpenAI-family targets and AzureMLChatTarget."
        ),
    )
