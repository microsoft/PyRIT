# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target instance models.

Targets have two concepts:
- Types: Static metadata bundled with frontend (from registry)
- Instances: Runtime objects created via API with specific configuration

This module defines the Instance models for runtime target management.
"""

from typing import Any, Literal, Optional

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
    endpoint: Optional[str] = Field(None, description="Target endpoint URL")
    model_name: Optional[str] = Field(None, description="Model or deployment name used in API calls")
    underlying_model_name: Optional[str] = Field(
        None, description="Underlying model name if different (e.g., 'gpt-4o')"
    )
    temperature: Optional[float] = Field(None, description="Temperature parameter for generation")
    top_p: Optional[float] = Field(None, description="Top-p parameter for generation")
    max_requests_per_minute: Optional[int] = Field(None, description="Maximum requests per minute")
    capabilities: TargetCapabilitiesInfo = Field(..., description="Structured snapshot of target capabilities")
    target_specific_params: Optional[dict[str, Any]] = Field(None, description="Additional target-specific parameters")


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


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
