# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of an attack technique's identifier."""

from __future__ import annotations

from pydantic import Field

from pyrit.models.identifiers.attack_identifier import (  # noqa: TC001
    AttackIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.seed_identifier import (  # noqa: TC001
    SeedIdentifier,  # runtime-required by Pydantic field annotations
)


class AttackTechniqueIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of an ``AttackTechnique``'s ``ComponentIdentifier``.

    Promotes the attack strategy child (``attack``) and the optional technique
    seeds (``technique_seeds``).
    """

    #: The attack strategy that defines the technique.
    attack: AttackIdentifier | None = None
    #: Optional seeds that specialize the technique.
    technique_seeds: list[SeedIdentifier] = Field(default_factory=list)
