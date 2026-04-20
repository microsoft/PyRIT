# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from collections.abc import Sequence
from typing import Any, Optional, TypeVar

from pyrit.auth import get_azure_openai_auth
from pyrit.common import apply_defaults
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackScoringConfig,
    AttackStrategy,
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.models import SeedAttackGroup, SeedGroup
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

AttackStrategyT = TypeVar("AttackStrategyT", bound="AttackStrategy[Any, Any]")


class ContentHarmsDatasetConfiguration(DatasetConfiguration):
    """
    Dataset configuration for content harms that loads seed groups by harm category.

    This subclass overrides the default loading behavior to use harm category pattern
    matching instead of exact dataset name matching. When scenario_composites are provided,
    it filters datasets to only those matching the selected harm strategies.
    """

    def get_seed_groups(self) -> dict[str, list[SeedGroup]]:
        """
        Get seed groups filtered by harm strategies from stored scenario_composites.

        When scenario_composites are set, this filters to only include datasets
        matching the selected harm strategies and returns harm strategy names as keys.

        Returns:
            Dict[str, List[SeedGroup]]: Dictionary mapping harm strategy names to their
                seed groups, filtered by the selected harm strategies.
        """
        result = super().get_seed_groups()

        if self._scenario_strategies is None:
            return result

        # Extract selected harm strategies
        selected_harms = {s.value for s in self._scenario_strategies if isinstance(s, ContentHarmsStrategy)}

        # Filter to matching datasets and map keys to harm names
        mapped_result: dict[str, list[SeedGroup]] = {}
        for name, groups in result.items():
            matched_harm = next((harm for harm in selected_harms if harm in name), None)
            if matched_harm:
                mapped_result[matched_harm] = groups

        return mapped_result


class ContentHarmsStrategy(ScenarioStrategy):
    """
    ContentHarmsStrategy defines a set of strategies for testing model behavior
    across several different harm categories. The scenario is designed to provide quick
    feedback on model performance with respect to common harm types with the idea being that
    users will dive deeper into specific harm categories based on initial results.

    Each tag represents a different harm category that the model can be tested for.
    Specifying the all tag will include a comprehensive test suite covering all harm categories.
    Users can define objectives for each harm category via seed datasets or use the default datasets
    provided with PyRIT.

    """

    ALL = ("all", {"all"})

    Hate = ("hate", set[str]())
    Fairness = ("fairness", set[str]())
    Violence = ("violence", set[str]())
    Sexual = ("sexual", set[str]())
    Harassment = ("harassment", set[str]())
    Misinformation = ("misinformation", set[str]())
    Leakage = ("leakage", set[str]())


class ContentHarms(Scenario):
    """

    Content Harms Scenario implementation for PyRIT.

    This scenario contains various harm-based checks that you can run to get a quick idea about model behavior
    with respect to certain harm categories.
    """

    VERSION: int = 1

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Get the strategy enum class for this scenario.

        Returns:
            Type[ScenarioStrategy]: The ContentHarmsStrategy enum class.
        """
        return ContentHarmsStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Get the default strategy used when no strategies are specified.

        Returns:
            ScenarioStrategy: ContentHarmsStrategy.ALL
        """
        return ContentHarmsStrategy.ALL

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        Returns:
            DatasetConfiguration: Configuration with all content harm datasets.
        """
        return ContentHarmsDatasetConfiguration(
            dataset_names=[
                "airt_hate",
                "airt_fairness",
                "airt_violence",
                "airt_sexual",
                "airt_harassment",
                "airt_misinformation",
                "airt_leakage",
            ],
            max_dataset_size=4,
        )

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: Optional[PromptChatTarget] = None,
        objective_scorer: Optional[TrueFalseScorer] = None,
        scenario_result_id: Optional[str] = None,
    ):
        """
        Initialize the Content Harms Scenario.

        Args:
            adversarial_chat (Optional[PromptChatTarget]): Additionally used for scoring defaults.
                If not provided, a default OpenAI target will be created using environment variables.
            objective_scorer (Optional[TrueFalseScorer]): Scorer to evaluate attack success.
                If not provided, creates a default composite scorer using Azure Content Filter
                and SelfAsk Refusal scorers.
            scenario_result_id (Optional[str]): Optional ID of an existing scenario result to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )
        self._adversarial_chat = adversarial_chat if adversarial_chat else self._get_default_adversarial_target()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=ContentHarmsStrategy,
            scenario_result_id=scenario_result_id,
        )

    def _get_default_adversarial_target(self) -> OpenAIChatTarget:
        endpoint = os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT")
        return OpenAIChatTarget(
            endpoint=endpoint,
            api_key=get_azure_openai_auth(endpoint),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            temperature=1.2,
        )

    def _resolve_seed_groups_by_harm(self) -> dict[str, list[SeedAttackGroup]]:
        """
        Resolve seed groups from dataset configuration.

        Returns:
            Dict[str, List[SeedAttackGroup]]: Dictionary mapping content harm strategy names to their
                seed attack groups.
        """
        # Set scenario_composites on the config so get_seed_attack_groups can filter by strategy
        self._dataset_config._scenario_strategies = self._scenario_strategies
        return self._dataset_config.get_seed_attack_groups()

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Retrieve the list of AtomicAttack instances for harm strategies.

        Returns:
            List[AtomicAttack]: The list of AtomicAttack instances for harm strategies.
        """
        seed_groups_by_harm = self._resolve_seed_groups_by_harm()

        atomic_attacks: list[AtomicAttack] = []
        for strategy, seed_groups in seed_groups_by_harm.items():
            atomic_attacks.extend(self._get_strategy_attacks(strategy=strategy, seed_groups=seed_groups))
        return atomic_attacks

    def _get_strategy_attacks(
        self,
        *,
        strategy: str,
        seed_groups: Sequence[SeedAttackGroup],
    ) -> list[AtomicAttack]:
        """
        Create AtomicAttack instances for a given harm strategy.

        Args:
            strategy (str): The harm strategy name to create attacks for.
            seed_groups (Sequence[SeedAttackGroup]): The seed attack groups associated with the harm dataset.

        Returns:
            list[AtomicAttack]: The constructed AtomicAttack instances for each attack type.

        Raises:
            ValueError: If scenario is not properly initialized.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        attacks: list[AtomicAttack] = [
            *self._get_single_turn_attacks(strategy=strategy, seed_groups=seed_groups),
            *self._get_multi_turn_attacks(strategy=strategy, seed_groups=seed_groups),
        ]

        return attacks

    def _get_single_turn_attacks(
        self,
        *,
        strategy: str,
        seed_groups: Sequence[SeedAttackGroup],
    ) -> list[AtomicAttack]:
        """
        Create single-turn AtomicAttack instances: RolePlayAttack and PromptSendingAttack.

        Args:
            strategy (str): The harm strategy name.
            seed_groups (Sequence[SeedAttackGroup]): Seed attack groups for this harm category.

        Returns:
            list[AtomicAttack]: The single-turn atomic attacks.
        """
        prompt_sending_attack = PromptSendingAttack(
            objective_target=self._objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=self._objective_scorer),
        )

        role_play_attack = RolePlayAttack(
            objective_target=self._objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=self._adversarial_chat),
            role_play_definition_path=RolePlayPaths.MOVIE_SCRIPT.value,
        )

        return [
            AtomicAttack(
                atomic_attack_name=strategy,
                attack_technique=AttackTechnique(attack=prompt_sending_attack),
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
            AtomicAttack(
                atomic_attack_name=strategy,
                attack_technique=AttackTechnique(attack=role_play_attack),
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
        ]

    def _get_multi_turn_attacks(
        self,
        *,
        strategy: str,
        seed_groups: Sequence[SeedAttackGroup],
    ) -> list[AtomicAttack]:
        """
        Create multi-turn AtomicAttack instances: ManyShotJailbreakAttack and TreeOfAttacksWithPruningAttack.

        Args:
            strategy (str): The harm strategy name.
            seed_groups (Sequence[SeedAttackGroup]): Seed attack groups for this harm category.

        Returns:
            list[AtomicAttack]: The multi-turn atomic attacks.
        """
        many_shot_jailbreak_attack = ManyShotJailbreakAttack(
            objective_target=self._objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=self._objective_scorer),
        )

        tap_attack = TreeOfAttacksWithPruningAttack(
            objective_target=self._objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=self._adversarial_chat),
        )

        return [
            AtomicAttack(
                atomic_attack_name=strategy,
                attack_technique=AttackTechnique(attack=many_shot_jailbreak_attack),
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
            AtomicAttack(
                atomic_attack_name=strategy,
                attack_technique=AttackTechnique(attack=tap_attack),
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
        ]
