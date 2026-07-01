# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a scenario's identifier."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pyrit.models.identifiers.component_identifier import ComponentIdentifier, JSONValue
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

    Unifies the two former "scenario identifier" concepts into one type: the
    build-contract projection the registry reads (via ``Param.*`` markers) and the
    per-run persistence record stored on a ``ScenarioResult``.

    Declares the scenario's two run-resolved reference slots — ``objective_target``
    (a ``PromptTarget``) and ``objective_scorer`` (a ``Scorer``) — so the registry
    can resolve them by name from the target/scorer registries when building a
    scenario. In a persisted run these slots are typically left unset (the
    ``ScenarioResult`` tracks the concrete target/scorer identifiers separately);
    they exist so the class-level build contract is derivable.

    The persistence metadata — ``name`` (the scenario class name), ``version``,
    ``description``, and ``init_data`` (the resolved parameter snapshot used for
    resume) — is exposed through read-only properties over ``class_name`` and the
    ``attributes`` bucket, so the identifier stays a frozen, content-addressed
    value while preserving the accessor surface memory / output / resume rely on.
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
        """The scenario class name (persistence surface over ``class_name``)."""
        return self.class_name

    @property
    def version(self) -> int:
        """The scenario definition version (defaults to 1)."""
        raw = self.attributes.get("version")
        return int(raw) if raw is not None else 1

    @property
    def description(self) -> str:
        """The scenario description (defaults to an empty string)."""
        return str(self.attributes.get("description") or "")

    @property
    def init_data(self) -> dict[str, Any] | None:
        """The resolved parameter snapshot used for resume, or ``None``."""
        raw = self.attributes.get("init_data")
        return raw if isinstance(raw, dict) else None

    @classmethod
    def for_scenario(
        cls,
        *,
        scenario_class_name: str,
        scenario_class_module: str = "unknown",
        version: int = 1,
        description: str = "",
        init_data: dict[str, Any] | None = None,
        pyrit_version: str | None = None,
        objective_target: TargetIdentifier | None = None,
        objective_scorer: ScorerIdentifier | None = None,
    ) -> ScenarioIdentifier:
        """
        Build a ``ScenarioIdentifier`` from scenario persistence metadata.

        Args:
            scenario_class_name (str): The scenario class name (stored as ``class_name``).
            scenario_class_module (str): The scenario class module (stored as ``class_module``).
            version (int): The scenario definition version.
            description (str): The scenario description.
            init_data (dict[str, Any] | None): The resolved parameter snapshot for resume.
            pyrit_version (str | None): Override for the stored pyrit version; ``None``
                uses the current installed version.
            objective_target (TargetIdentifier | None): Optional resolved target reference.
            objective_scorer (ScorerIdentifier | None): Optional resolved scorer reference.

        Returns:
            ScenarioIdentifier: The frozen identifier carrying the metadata in ``attributes``.
        """
        attributes: dict[str, JSONValue] = {"version": int(version)}
        if description:
            attributes["description"] = description
        if init_data is not None:
            attributes["init_data"] = init_data

        kwargs: dict[str, Any] = {
            "class_name": scenario_class_name,
            "class_module": scenario_class_module,
            "attributes": attributes,
            "objective_target": objective_target,
            "objective_scorer": objective_scorer,
        }
        if pyrit_version is not None:
            kwargs["pyrit_version"] = pyrit_version
        return cls(**kwargs)

    def with_init_data(self, init_data: dict[str, Any]) -> ScenarioIdentifier:
        """
        Return a copy of this identifier with ``init_data`` set in ``attributes``.

        The identifier is frozen, so mutating ``init_data`` in place is not
        possible; this returns a new value with the snapshot applied.

        Args:
            init_data (dict[str, Any]): The resolved parameter snapshot to store.

        Returns:
            ScenarioIdentifier: A new identifier carrying ``init_data``.
        """
        return type(self).for_scenario(
            scenario_class_name=self.class_name,
            scenario_class_module=self.class_module,
            version=self.version,
            description=self.description,
            init_data=init_data,
            pyrit_version=self.pyrit_version,
            objective_target=self.objective_target,
            objective_scorer=self.objective_scorer,
        )
