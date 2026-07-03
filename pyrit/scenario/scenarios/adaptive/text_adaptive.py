# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``TextAdaptive`` — text adaptive scenario.

Picks attack techniques per-objective using an epsilon-greedy selector
informed by observed success rates. Runs up to ``max_attempts_per_objective``
techniques per objective and stops early on success. ``prompt_sending`` is
excluded from the adaptive technique pool and runs as the baseline comparison
instead.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.models.parameter import Parameter
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration, DatasetAttackConfiguration
from pyrit.scenario.scenarios.adaptive.adaptive_scenario import AdaptiveScenario

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.scenario.scenarios.adaptive.selectors import TechniqueSelector
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

# Techniques excluded from the adaptive technique pool. These run as the
# baseline comparison rather than as adversarial moves the selector chooses.
_EXCLUDED_TECHNIQUES = frozenset({"prompt_sending"})


def _build_text_adaptive_strategy() -> type[ScenarioStrategy]:
    """
    Build the strategy enum from the core scenario-techniques catalog,
    excluding techniques that run as baseline.

    Returns:
        type[ScenarioStrategy]: The dynamically-built strategy enum class.

    Logs a warning if any name in ``_EXCLUDED_TECHNIQUES`` is not present
    in the current catalog. The exclusion is defensive — when the catalog
    does not contain the named technique, the filter is a no-op, and we
    surface that so a stale entry in the exclusion list (or a renamed
    catalog entry) doesn't silently break the intended exclusion.
    """
    # Local import: ``scenario_techniques`` imports ``pyrit.scenario.core``,
    # which transitively re-imports this module, so a top-level import would
    # form a cycle during ``pyrit.scenario`` package initialization.
    from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories

    all_factories = list(build_scenario_technique_factories())
    catalog_names = {factory.name for factory in all_factories}
    unmatched = _EXCLUDED_TECHNIQUES - catalog_names
    if unmatched:
        logger.warning(
            "TextAdaptive: _EXCLUDED_TECHNIQUES entries %s are not in the current "
            "scenario-techniques catalog %s; the exclusion is a no-op for those entries. "
            "Remove stale entries or update the catalog.",
            sorted(unmatched),
            sorted(catalog_names),
        )

    factories = [factory for factory in all_factories if factory.name not in _EXCLUDED_TECHNIQUES]

    return AttackTechniqueRegistry.build_strategy_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="TextAdaptiveStrategy",
        factories=factories,
        aggregate_tags={
            "default": TagQuery.any_of("default"),
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class TextAdaptive(AdaptiveScenario):
    """
    Adaptive text-attack scenario.

    Selects techniques per-objective via an epsilon-greedy selector over the
    set of selected strategies. ``prompt_sending`` runs as the baseline
    comparison and is excluded from the adaptive technique pool.
    """

    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    VERSION: ClassVar[int] = 1

    @classmethod
    def _atomic_attack_prefix(cls) -> str:
        """Return the prefix for per-objective atomic-attack names."""
        return "adaptive_text"

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """Return the strategy enum for this scenario, building it once on first access."""
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = _build_text_adaptive_strategy()
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """Return the default strategy aggregate (resolves to every ``default``-tagged technique)."""
        strategy_class = cls.get_strategy_class()
        return strategy_class("default")

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
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare custom parameters this scenario accepts from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run.
        """
        return [
            Parameter(
                name="max_attempts_per_objective",
                description="Max techniques tried per objective. Defaults to 3.",
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
