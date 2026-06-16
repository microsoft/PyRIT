# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a target's identifier."""

from __future__ import annotations

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier


class TargetIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``PromptTarget``'s ``ComponentIdentifier``.

    Promotes the common target params to typed fields; any other params stay in
    ``params``. Capabilities are not part of identity and are not surfaced here.

    Promotes the one child slot a target owns in its own constructor:
    ``targets`` (inner targets of a multi-target like ``RoundRobinTarget``),
    typed recursively as ``TargetIdentifier``.
    """

    #: Target endpoint URL.
    endpoint: str | None = None
    #: Model or deployment name used in API calls.
    model_name: str | None = None
    #: Underlying model name if different (e.g., "gpt-4o").
    underlying_model_name: str | None = None
    #: Temperature parameter for generation.
    temperature: float | None = None
    #: Top-p parameter for generation.
    top_p: float | None = None
    #: Maximum requests per minute.
    max_requests_per_minute: int | None = None
    #: Inner targets of a multi-target (e.g., ``RoundRobinTarget``), typed recursively.
    targets: list[TargetIdentifier] = Field(default_factory=list)
