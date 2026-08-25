# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a target's identifier."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.param_markers import Param
from pyrit.models.parameter import ComponentType


class TargetIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``PromptTarget``'s ``ComponentIdentifier``.

    Promotes the common target params to typed fields; any other params stay in
    ``params``. Message-handling capabilities are not part of identity and are not
    surfaced here. ``supported_auth_modes`` is the one non-identity fact surfaced:
    a ``Param.ClassAttr`` (``Evaluate.Exclude``) the registry reads off the class
    into ``TargetMetadata`` — never an identity input or a constructor argument.

    Promotes the one child slot a target owns in its own constructor:
    ``targets`` (inner targets of a multi-target like ``RoundRobinTarget``),
    typed recursively as ``TargetIdentifier``.

    ``Evaluate.*`` markers declare the behavioral projection used for the eval
    hash: operational params (``endpoint`` / ``model_name`` /
    ``max_requests_per_minute``) are excluded, ``underlying_model_name`` falls
    back to ``model_name``, and ``targets`` is a wrapper passthrough that is
    unwrapped so a multi-target hashes the same as its inner target.
    """

    component_type: ClassVar[ComponentType] = ComponentType.TARGET

    #: Target endpoint URL.
    endpoint: Annotated[str | None, Evaluate.Exclude()] = None
    #: Model or deployment name used in API calls.
    model_name: Annotated[str | None, Evaluate.Exclude()] = None
    #: Underlying model name if different (e.g., "gpt-4o").
    underlying_model_name: Annotated[str | None, Evaluate.Include(fallback="model_name")] = None
    #: Temperature parameter for generation.
    temperature: Annotated[float | None, Evaluate.Include()] = None
    #: Top-p parameter for generation.
    top_p: Annotated[float | None, Evaluate.Include()] = None
    #: Maximum requests per minute.
    max_requests_per_minute: Annotated[int | None, Evaluate.Exclude()] = None
    #: Inner targets of a multi-target (e.g., ``RoundRobinTarget``), typed
    #: recursively. An included constructor parameter (the ctor arg is also
    #: ``targets``, a list) resolved by name from the target registry.
    targets: Annotated[list[TargetIdentifier], Evaluate.Unwrap(), Param.Include()] = Field(default_factory=list)
    #: Auth modes this target type accepts, sourced from the ``supported_auth_modes``
    #: class attribute (not a constructor argument, not part of identity). Read by
    #: the registry so ``TargetMetadata`` can describe the class without building it.
    supported_auth_modes: Annotated[
        list[Literal["api_key", "identity"]] | None,
        Evaluate.Exclude(),
        Param.ClassAttr(attr_name="supported_auth_modes"),
    ] = None
