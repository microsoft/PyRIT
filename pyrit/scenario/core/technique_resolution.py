# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique


class TechniqueResolutionError(ValueError):
    """
    Raised when a selected scenario technique has no registered factory.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep working,
    mirroring ``DatasetConstraintError``.
    """


def resolve_technique_factories(
    *,
    context: ScenarioContext,
    extra_factories: dict[str, AttackTechniqueFactory] | None = None,
) -> dict[str, AttackTechniqueFactory]:
    """
    Resolve a run's selected techniques to their registered ``AttackTechniqueFactory`` instances.

    Reads the ``AttackTechniqueRegistry`` singleton and keeps only the factories whose name
    matches a selected technique, preserving selection order. Raises if any selected
    technique has no registered factory so the run cannot silently omit requested work.

    Args:
        context (ScenarioContext): The resolved runtime inputs for this run.
        extra_factories (dict[str, AttackTechniqueFactory] | None): Scenario-local factories
            merged on top of the registry before filtering, so a scenario can offer techniques
            without registering them globally. Entries override registry factories of the same
            name.

    Returns:
        dict[str, AttackTechniqueFactory]: Mapping of technique name to factory, ordered by
        the selected techniques.

    Raises:
        TechniqueResolutionError: If any selected technique has no registered factory.
    """
    return resolve_technique_factories_for_techniques(
        scenario_techniques=context.scenario_techniques,
        extra_factories=extra_factories,
    )


def resolve_technique_factories_for_techniques(
    *,
    scenario_techniques: Sequence[ScenarioTechnique],
    extra_factories: dict[str, AttackTechniqueFactory] | None = None,
) -> dict[str, AttackTechniqueFactory]:
    """
    Resolve selected concrete techniques to their canonical factories.

    Accepts any sequence of scenario techniques so callers without a full
    `ScenarioContext` (e.g. run-size estimators, dry runs, scenario builders) can
    inspect factory metadata or validate coverage.

    Args:
        scenario_techniques (Sequence[ScenarioTechnique]): Concrete techniques to resolve.
        extra_factories (dict[str, AttackTechniqueFactory] | None): Scenario-local factories
            merged on top of the registry. Entries override registry factories of the same name.

    Returns:
        dict[str, AttackTechniqueFactory]: Selected factories in technique order.

    Raises:
        TechniqueResolutionError: If any selected technique has no registered factory.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

    all_factories = dict(AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise())
    if extra_factories:
        all_factories.update(extra_factories)

    missing = list(dict.fromkeys(t.value for t in scenario_techniques if t.value not in all_factories))

    if missing:
        raise TechniqueResolutionError(
            "The following selected attack techniques have no registered factory: "
            f"{', '.join(missing)}. Register the techniques (or pass them via "
            "extra_factories) before starting the run."
        )

    return {technique.value: all_factories[technique.value] for technique in scenario_techniques}
