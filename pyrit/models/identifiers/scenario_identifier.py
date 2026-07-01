# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a scenario's identifier."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

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
    this declares only the scenario's identity and build contract — never its
    per-run persistence record.

    Declares the scenario's two run-resolved reference slots — ``objective_target``
    (a ``PromptTarget``) and ``objective_scorer`` (a ``Scorer``) — so the registry
    can resolve them by name from the target/scorer registries when building a
    scenario. In a persisted run these slots are typically left unset (the
    ``ScenarioResult`` tracks the concrete target/scorer identifiers separately);
    they exist so the class-level build contract is derivable.

    The scenario definition ``version`` is identity-bearing state (a v1 and a v2 of
    the same scenario are different identities) and lives in the ``attributes``
    bucket, surfaced through a read-only property. Non-identity metadata — the
    human-readable ``description`` (the class docstring) and ``init_data`` (the
    resolved parameter snapshot used for resume) — is *not* stored here; it lives
    on the ``ScenarioResult`` persistence aggregate.
    """

    component_type: ClassVar[ComponentType] = ComponentType.SCENARIO

    #: Target the scenario attacks. Run-resolved reference (constructor/`self`
    #: input) resolved by name from the target registry; unset on persisted runs.
    objective_target: Annotated[TargetIdentifier | None, Evaluate.Include(), Param.Include()] = None
    #: Primary scorer the scenario evaluates with. Run-resolved reference resolved
    #: by name from the scorer registry; unset on persisted runs.
    objective_scorer: Annotated[ScorerIdentifier | None, Evaluate.Include(), Param.Include()] = None

    @property
    def name(self) -> str:
        """The scenario class name (alias over ``class_name``)."""
        return self.class_name

    @property
    def version(self) -> int:
        """The scenario definition version (identity-bearing; defaults to 1)."""
        raw = self.attributes.get("version")
        return int(raw) if raw is not None else 1

    @classmethod
    def for_scenario(
        cls,
        *,
        scenario_class_name: str,
        scenario_class_module: str = "unknown",
        version: int = 1,
        pyrit_version: str | None = None,
        objective_target: TargetIdentifier | None = None,
        objective_scorer: ScorerIdentifier | None = None,
    ) -> ScenarioIdentifier:
        """
        Build a ``ScenarioIdentifier`` from a scenario's identity fields.

        The definition ``version`` is stored in ``attributes`` (identity-bearing
        state), keeping the projection frozen and content-addressed. Non-identity
        metadata (description, resume snapshot) belongs on the ``ScenarioResult``,
        not here.

        Args:
            scenario_class_name (str): The scenario class name (stored as ``class_name``).
            scenario_class_module (str): The scenario class module (stored as ``class_module``).
            version (int): The scenario definition version.
            pyrit_version (str | None): Override for the stored pyrit version; ``None``
                uses the current installed version.
            objective_target (TargetIdentifier | None): Optional resolved target reference.
            objective_scorer (ScorerIdentifier | None): Optional resolved scorer reference.

        Returns:
            ScenarioIdentifier: The frozen identifier carrying ``version`` in ``attributes``.
        """
        kwargs: dict[str, Any] = {
            "class_name": scenario_class_name,
            "class_module": scenario_class_module,
            "attributes": {"version": int(version)},
            "objective_target": objective_target,
            "objective_scorer": objective_scorer,
        }
        if pyrit_version is not None:
            kwargs["pyrit_version"] = pyrit_version
        return cls(**kwargs)
