# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``TextAdaptive`` — text adaptive scenario.

Picks attack techniques per-objective using an epsilon-greedy selector
informed by observed success rates. Runs up to ``max_attempts_per_objective``
techniques per objective and stops early on success. The shared catalog omits
``prompt_sending`` because scenarios provide it through their baseline policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.models.parameter import Parameter
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration, DatasetAttackConfiguration
from pyrit.scenario.scenarios.adaptive.adaptive_scenario import AdaptiveScenario

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.scenario.scenarios.adaptive.selectors import TechniqueSelector
    from pyrit.score import TrueFalseScorer


def _build_text_adaptive_technique() -> type[ScenarioTechnique]:
    """
    Build the technique enum from the scenario-techniques catalog.

    Returns:
        type[ScenarioTechnique]: The dynamically-built technique enum class.
    """
    # Local import: ``techniques`` imports ``pyrit.scenario.core``,
    # which transitively re-imports this module, so a top-level import would
    # form a cycle during ``pyrit.scenario`` package initialization.
    from pyrit.setup.initializers.techniques import build_technique_factories

    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="TextAdaptiveTechnique",
        factories=build_technique_factories(),
        default_names={"role_play_movie_script", "many_shot"},
    )


class TextAdaptive(AdaptiveScenario):
    """
    Selects an attack technique for each objective using an epsilon-greedy
    strategy informed by prior success rates. The scenario stops after a
    successful attack or after ``max_attempts_per_objective`` attempts.
    """

    _cached_technique_class: ClassVar[type[ScenarioTechnique] | None] = None

    VERSION: ClassVar[int] = 1

    @classmethod
    def _atomic_attack_prefix(cls) -> str:
        """Return the prefix for per-objective atomic-attack names."""
        return "adaptive_text"

    @classmethod
    def get_technique_class(cls) -> type[ScenarioTechnique]:
        """Return the technique enum for this scenario, building it once on first access."""
        if cls._cached_technique_class is None:
            cls._cached_technique_class = _build_text_adaptive_technique()
        return cls._cached_technique_class

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return the dataset names this scenario expects when no override is provided."""
        return [
            "airt_hate",
            "airt_fairness",
            "airt_violence",
            "airt_sexual",
            "airt_harassment",
            "airt_misinformation",
            "airt_leakage",
        ]

    @classmethod
    def default_dataset_config(cls) -> DatasetAttackConfiguration:
        """Return the default dataset config (required datasets, capped at 4 per dataset)."""
        return CompoundDatasetAttackConfiguration.per_dataset(dataset_names=cls.required_datasets(), max_dataset_size=4)

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare custom parameters this scenario accepts from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run.
        """
        return [
            Parameter(
                name="max_attempts_per_objective",
                description=(
                    "Maximum different compatible techniques Adaptive may try for one objective, stopping after "
                    "the first success. This is separate from retries."
                ),
                param_type=int,
                default=3,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        selector: TechniqueSelector | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Args:
            objective_scorer (TrueFalseScorer | None): Scorer used to judge each
                response. Defaults to the composite scorer from the base class.
            selector (TechniqueSelector | None): Pre-built selector. When ``None``
                (default) an ``EpsilonGreedyTechniqueSelector`` is created
                with default settings. Pass a custom instance to tune
                ``epsilon`` or ``random_seed``.
            scenario_result_id (str | None): ID of an existing ``ScenarioResult`` to resume.
        """
        super().__init__(
            objective_scorer=objective_scorer,
            selector=selector,
            scenario_result_id=scenario_result_id,
        )
