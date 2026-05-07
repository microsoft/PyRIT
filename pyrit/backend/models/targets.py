# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target instance models.

Targets have two concepts:
- Types: Static metadata bundled with frontend (from registry)
- Instances: Runtime objects created via API with specific configuration

This module defines the Instance models for runtime target management.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo


class TargetCapabilitiesInfo(BaseModel):
    """Structured capability flags for a target instance."""

    supports_multi_turn: bool = Field(..., description="Whether the target supports multi-turn conversation history")
    supports_multi_message_pieces: bool = Field(
        ..., description="Whether the target supports multiple message pieces in a single request"
    )
    supports_json_schema: bool = Field(
        ..., description="Whether the target supports constraining output to a JSON schema"
    )
    supports_json_output: bool = Field(..., description="Whether the target supports JSON output format")
    supports_editable_history: bool = Field(
        ..., description="Whether the target allows the attack history to be modified"
    )
    supports_system_prompt: bool = Field(..., description="Whether the target supports system prompts")
    supported_input_modalities: list[str] = Field(
        default_factory=list,
        description="Flattened, sorted list of supported input modality data types (e.g., 'text', 'image_path')",
    )
    supported_output_modalities: list[str] = Field(
        default_factory=list,
        description="Flattened, sorted list of supported output modality data types (e.g., 'text', 'audio_path')",
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
    supports_multi_turn: bool = Field(True, description="Whether the target supports multi-turn conversation history")
    capabilities: Optional[TargetCapabilitiesInfo] = Field(None, description="Structured capability flags")
    target_specific_params: Optional[dict[str, Any]] = Field(None, description="Additional target-specific parameters")


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class CreateTargetRequest(BaseModel):
    """Request to create a new target instance."""

    type: str = Field(..., description="Target type (e.g., 'OpenAIChatTarget')")
    params: dict[str, Any] = Field(default_factory=dict, description="Target constructor parameters")
