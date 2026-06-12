# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target instance catalog models.

Targets have two concepts:

- Types: Static metadata bundled with the frontend (from the registry).
- Instances: Runtime objects created via the API with specific configuration.

The ``TargetInstance`` model is the wire-format snapshot for a runtime
target, used by both the backend (as a REST response payload) and external
REST clients (the CLI today, future external clients tomorrow).

Per-field documentation strings (``Field(..., description=...)``) deliberately
live in the backend layer rather than here — see ``pyrit.models.MessagePiece``
vs ``pyrit.backend.models.attacks.MessagePieceView`` for the same split.
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

    supports_multi_turn: bool = False
    supports_multi_message_pieces: bool = False
    supports_json_schema: bool = False
    supports_json_output: bool = False
    supports_editable_history: bool = False
    supports_system_prompt: bool = False
    supported_input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    supported_output_modalities: list[str] = Field(default_factory=lambda: ["text"])


class TargetInstance(BaseModel):
    """
    A runtime target instance.

    Created either by an initializer (at startup) or by user (via API).
    Also used as the create-target response (same shape as GET).
    """

    target_registry_name: str
    target_type: str
    endpoint: str | None = None
    model_name: str | None = None
    underlying_model_name: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_requests_per_minute: int | None = None
    capabilities: TargetCapabilitiesInfo
    target_specific_params: dict[str, Any] | None = None
    inner_targets: list["TargetInstance"] | None = None
    identifier_hash: str | None = None
