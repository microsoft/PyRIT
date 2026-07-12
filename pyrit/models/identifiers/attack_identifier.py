# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of an attack strategy's identifier."""

from __future__ import annotations

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.converter_identifier import (  # noqa: TC001
    ConverterIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.identifiers.scorer_identifier import (  # noqa: TC001
    ScorerIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.identifiers.target_identifier import (  # noqa: TC001
    TargetIdentifier,  # runtime-required by Pydantic field annotations
)


class AttackIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of an ``AttackStrategy``'s ``ComponentIdentifier``.

    Promotes the effective adversarial system/seed prompts and the attack's own
    child slots — objective target, adversarial chat target, objective scorer, and
    the request/response converter pipelines.
    """

    #: Effective adversarial system prompt text, if the strategy uses one.
    adversarial_system_prompt: str | None = None
    #: Effective adversarial seed prompt text, if the strategy uses one.
    adversarial_seed_prompt: str | None = None
    #: The objective target the attack drives.
    objective_target: TargetIdentifier | None = None
    #: The adversarial chat target, if the strategy uses one.
    adversarial_chat: TargetIdentifier | None = None
    #: The objective scorer, if the strategy uses one.
    objective_scorer: ScorerIdentifier | None = None
    #: Request-side converter pipeline.
    request_converters: list[ConverterIdentifier] = Field(default_factory=list)
    #: Response-side converter pipeline.
    response_converters: list[ConverterIdentifier] = Field(default_factory=list)
