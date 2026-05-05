# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.common.path import DATASETS_PATH, SCORER_SEED_PROMPT_PATH
from pyrit.executor.attack import (
    AttackConverterConfig,
    PromptSendingAttack,
)
from pyrit.prompt_converter import AddImageTextConverter, FirstLetterConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
    AttackTechniqueSpec,
)
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.score import (
    SelfAskRefusalScorer,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

if TYPE_CHECKING:
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Leakage-specific technique catalog
# ---------------------------------------------------------------------------

_BLANK_IMAGE_PATH = str(DATASETS_PATH / "seed_datasets" / "local" / "examples" / "blank_canvas.png")

LEAKAGE_TECHNIQUES: list[AttackTechniqueSpec] = [
    AttackTechniqueSpec(
        name="first_letter",
        attack_class=PromptSendingAttack,
        strategy_tags=["single_turn", "default"],
        extra_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=PromptConverterConfiguration.from_converters(converters=[FirstLetterConverter()])
            ),
        },
    ),
    AttackTechniqueSpec(
        name="image",
        attack_class=PromptSendingAttack,
        strategy_tags=["single_turn", "default"],
        extra_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=PromptConverterConfiguration.from_converters(
                    converters=[AddImageTextConverter(img_to_add=_BLANK_IMAGE_PATH)]
                )
            ),
        },
    ),
]


def _build_leakage_strategy() -> type[ScenarioStrategy]:
    """
    Build the Leakage strategy class dynamically from core + leakage-specific techniques.

    Combines core SCENARIO_TECHNIQUES with leakage-unique techniques (first_letter, image)
    to provide a full set of attack strategies.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

    all_specs = SCENARIO_TECHNIQUES + LEAKAGE_TECHNIQUES
    return AttackTechniqueRegistry.build_strategy_class_from_specs(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="LeakageStrategy",
        specs=all_specs,
        aggregate_tags={
            "default": TagQuery.any_of("default"),
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class Leakage(Scenario):
    """
    Leakage scenario implementation for PyRIT.

    This scenario tests how susceptible models are to leaking training data, PII, intellectual
    property, or other confidential information. Uses the registry/factory pattern to
    construct attack techniques.
    """

    VERSION: int = 2
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """Return the dynamically generated strategy class, building it on first access."""
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = _build_leakage_strategy()
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy member (DEFAULT).

        Returns:
            ScenarioStrategy: The DEFAULT strategy value.
        """
        strategy_class = cls.get_strategy_class()
        return strategy_class("default")

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_leakage"]

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """Return the default dataset configuration for this scenario."""
        return DatasetConfiguration(dataset_names=["airt_leakage"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the leakage scenario.

        Args:
            objective_scorer: Scorer for evaluating leakage detection.
                Defaults to a composite scorer (leakage detection + refusal backstop).
            scenario_result_id: Optional ID of an existing scenario result to resume.
        """
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()

        super().__init__(
            version=self.VERSION,
            strategy_class=self.get_strategy_class(),
            objective_scorer=objective_scorer,
            include_default_baseline=True,
            scenario_result_id=scenario_result_id,
        )

    def _get_default_objective_scorer(self) -> TrueFalseCompositeScorer:
        """
        Retrieve the default objective scorer.

        Uses a composite scorer:
        1. SelfAskTrueFalseScorer with leakage detection prompt.
        2. Inverted SelfAskRefusalScorer as backstop (response must not be a refusal).

        The chat target is resolved from the base class default scorer if available,
        otherwise falls back to OpenAIChatTarget().

        Returns:
            TrueFalseCompositeScorer: A composite scorer combining leakage detection and refusal backstop.
        """
        base_scorer = super()._get_default_objective_scorer()
        chat_target = base_scorer.get_chat_target() or OpenAIChatTarget()

        presence_of_leakage = SelfAskTrueFalseScorer(
            chat_target=chat_target,
            true_false_question_path=SCORER_SEED_PROMPT_PATH / "true_false_question" / "leakage.yaml",
        )

        backstop = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=chat_target))

        return TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND, scorers=[presence_of_leakage, backstop]
        )

    def _get_attack_technique_factories(self) -> dict[str, AttackTechniqueFactory]:
        """
        Return core + leakage-specific attack technique factories.

        Gets core factories from the base class, then builds leakage-specific
        factories locally without registering them in the global registry.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique names to their factories.
        """
        factories = super()._get_attack_technique_factories()

        for spec in LEAKAGE_TECHNIQUES:
            factories[spec.name] = AttackTechniqueRegistry.build_factory_from_spec(spec)

        return factories
