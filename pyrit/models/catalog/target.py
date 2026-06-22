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
"""

from typing import Any

from pydantic import BaseModel, Field


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
    supports_streaming_audio: bool = Field(False, description="Target supports streaming audio input/output")
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
