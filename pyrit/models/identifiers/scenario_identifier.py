# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a scenario's identifier."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.param_markers import Param
from pyrit.models.identifiers.scorer_identifier import (  # noqa: TC001
    ScorerIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.identifiers.target_identifier import (  # noqa: TC001
    TargetIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.parameter import ComponentType


class ScenarioIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``Scenario``'s ``ComponentIdentifier``.

    Like the sibling projections (``TargetIdentifier`` / ``ScorerIdentifier``),
    this is a build-time projection produced by the scenario registry — never a
    per-run persistence record. The persisted run facts (scenario name, version,
    description, and the resolved-parameter resume snapshot) live as denormalized
    fields on the ``ScenarioResult`` aggregate, not here.

    Promotes the scenario's behavioral identity to typed ``params`` fields that
    feed both the content and eval hash: the definition ``version`` and the
    resolved ``techniques`` / ``datasets`` the scenario runs (a v1 vs a v2, or a
    different technique / dataset selection, is a different identity). The two
    run-resolved reference slots — ``objective_target`` (a ``PromptTarget``) and
    ``objective_scorer`` (a ``Scorer``) — are promoted children the registry
    resolves by name from the target / scorer registries when building a scenario.
    """

    component_type: ClassVar[ComponentType] = ComponentType.SCENARIO

    #: Scenario definition version. Behavioral identity (a v1 and a v2 of the same
    #: scenario are different identities); not a constructor input.
    version: Annotated[int | None, Evaluate.Include(), Param.Exclude()] = None
    #: Resolved technique names the scenario runs. Behavioral identity; not a
    #: constructor input (the registry populates it from the selected strategies).
    techniques: Annotated[list[str] | None, Evaluate.Include(), Param.Exclude()] = None
    #: Resolved dataset names the scenario runs. Behavioral identity; not a
    #: constructor input (the registry populates it from the dataset config).
    datasets: Annotated[list[str] | None, Evaluate.Include(), Param.Exclude()] = None
    #: Target the scenario attacks. Run-resolved reference resolved by name from
    #: the target registry.
    objective_target: Annotated[TargetIdentifier | None, Evaluate.Include(), Param.Include()] = Field(default=None)
    #: Primary scorer the scenario evaluates with. Run-resolved reference resolved
    #: by name from the scorer registry.
    objective_scorer: Annotated[ScorerIdentifier | None, Evaluate.Include(), Param.Include()] = Field(default=None)
