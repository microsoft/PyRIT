# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from typing import Any, Dict, List, Optional, Sequence, Type, TypeVar

from pyrit.common import apply_defaults
from pyrit.executor.attack import (
    AttackScoringConfig,
    AttackStrategy,
    ManyShotJailbreakAttack,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
)
from pyrit.models import SeedAttackGroup, SeedGroup
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import (
    ScenarioCompositeStrategy,
    ScenarioStrategy,
)
from pyrit.score import SelfAskRefusalScorer, TrueFalseInverterScorer, TrueFalseScorer

AttackStrategyT = TypeVar("AttackStrategyT", bound="AttackStrategy[Any, Any]")


class ContentHarmsDatasetConfiguration(DatasetConfiguration):
    """
    Dataset configuration for content harms that loads seed groups by harm category.

    This subclass overrides the default loading behavior to use harm category pattern
    matching instead of exact dataset name matching. When scenario_composites are provided,
    it filters datasets to only those matching the selected harm strategies.
    """

    def get_seed_groups(self) -> Dict[str, List[SeedGroup]]:
        """
        Get seed groups filtered by harm strategies from stored scenario_composites.

        When scenario_composites are set, this filters to only include datasets
        matching the selected harm strategies and returns harm strategy names as keys.

        Returns:
            Dict[str, List[SeedGroup]]: Dictionary mapping harm strategy names to their
                seed groups, filtered by the selected harm strategies.
        """
        result = super().get_seed_groups()

        if self._scenario_composites is None:
            return result

        # Extract selected harm strategies
        selected_harms = ScenarioCompositeStrategy.extract_single_strategy_values(
            self._scenario_composites, strategy_type=ContentHarmsStrategy
        )

        # Filter to matching datasets and map keys to harm names
        mapped_result: Dict[str, List[SeedGroup]] = {}
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
    Users can defined objectives for each harm category via seed datasets or use the default datasets
    provided with PyRIT.
    For each harm category, the scenario will run a RolePlayAttack, ManyShotJailbreakAttack,
    PromptSendingAttack, and RedTeamingAttack for each objective in the dataset.
    to evaluate model behavior.
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

    version: int = 1

    @classmethod
    def get_strategy_class(cls) -> Type[ScenarioStrategy]:
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
        objectives_by_harm: Optional[Dict[str, Sequence[SeedGroup]]] = None,
    ):
        """
        Initialize the Content Harms Scenario.

        Args:
            adversarial_chat (Optional[PromptChatTarget]): Additionally used for scoring defaults.
                If not provided, a default OpenAI target will be created using environment variables.
            objective_scorer (Optional[TrueFalseScorer]): Scorer to evaluate attack success.
                If not provided, creates a default composite scorer using Azure Content Filter
                and SelfAsk Refusal scorers.
                seed_dataset_prefix (Optional[str]): Prefix of the dataset to use to retrieve the objectives.
                This will be used to retrieve the appropriate seed groups from CentralMemory. If not provided,
                defaults to "content_harm".
            scenario_result_id (Optional[str]): Optional ID of an existing scenario result to resume.
            objectives_by_harm (Optional[Dict[str, Sequence[SeedGroup]]]): A dictionary mapping harm strategies
                to their corresponding SeedGroups. If not provided, default seed groups will be loaded from datasets.
        """
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)
        self._adversarial_chat = adversarial_chat if adversarial_chat else self._get_default_adversarial_target()

        self._objective_scorer: TrueFalseScorer = objective_scorer if objective_scorer else self._get_default_scorer()

        super().__init__(
            name="Content Harms",
            version=self.version,
            objective_scorer=self._objective_scorer,
            strategy_class=ContentHarmsStrategy,
            scenario_result_id=scenario_result_id,
        )
        self._objectives_by_harm = objectives_by_harm

    def _get_default_adversarial_target(self) -> OpenAIChatTarget:
        return OpenAIChatTarget(
            endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
            api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            temperature=1.2,
        )

    def _get_default_scorer(self) -> TrueFalseInverterScorer:
        return TrueFalseInverterScorer(
            scorer=SelfAskRefusalScorer(
                chat_target=OpenAIChatTarget(
                    endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
                    api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
                    model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
                    temperature=0.9,
                )
            ),
        )

    async def _get_atomic_attacks_async(self) -> List[AtomicAttack]:
        """
        Retrieve the list of AtomicAttack instances for harm strategies.

        Returns:
            List[AtomicAttack]: The list of AtomicAttack instances for harm strategies.
        """
        # Set scenario_composites on the config so get_seed_attack_groups can filter by strategy
        self._dataset_config._scenario_composites = self._scenario_composites

        # Get seed attack groups by harm strategy, already filtered by scenario_composites
        seed_groups_by_harm = self._dataset_config.get_seed_attack_groups()

        atomic_attacks: List[AtomicAttack] = []
        for strategy, seed_groups in seed_groups_by_harm.items():
            atomic_attacks.extend(self._get_strategy_attacks(strategy=strategy, seed_groups=seed_groups))
        return atomic_attacks

    def _get_strategy_attacks(
        self,
        strategy: str,
        seed_groups: Sequence[SeedAttackGroup],
    ) -> List[AtomicAttack]:
        """
        Create AtomicAttack instances for a given harm strategy. RolePlayAttack, ManyShotJailbreakAttack,
        PromptSendingAttack, and RedTeamingAttack are run for all harm strategies.

        Args:
            strategy (ScenarioCompositeStrategy): The strategy to create the attack from.
            seed_groups (List[SeedAttackGroup]): The seed attack groups associated with the harm dataset.

        Returns:
            List[AtomicAttack]: The constructed AtomicAttack instances for each attack type.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        assert self._objective_target is not None

        prompt_sending_attack = PromptSendingAttack(
            objective_target=self._objective_target,
            attack_scoring_config=self._scorer_config,
        )

        role_play_attack = RolePlayAttack(
            objective_target=self._objective_target,
            adversarial_chat=self._adversarial_chat,
            role_play_definition_path=RolePlayPaths.MOVIE_SCRIPT.value,
        )

        many_shot_jailbreak_attack = ManyShotJailbreakAttack(
            objective_target=self._objective_target,
            attack_scoring_config=self._scorer_config,
        )

        attacks = [
            AtomicAttack(
                atomic_attack_name=strategy,
                attack=prompt_sending_attack,
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
            AtomicAttack(
                atomic_attack_name=strategy,
                attack=role_play_attack,
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
            AtomicAttack(
                atomic_attack_name=strategy,
                attack=many_shot_jailbreak_attack,
                seed_groups=list(seed_groups),
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
            ),
        ]

        # Only add MultiPromptSendingAttack for seed_groups that have user messages
        seed_groups_with_messages = [sg for sg in seed_groups if sg.user_messages]
        if seed_groups_with_messages:
            multi_prompt_sending_attack = MultiPromptSendingAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
            )
            attacks.append(
                AtomicAttack(
                    atomic_attack_name=strategy,
                    attack=multi_prompt_sending_attack,
                    seed_groups=seed_groups_with_messages,
                    adversarial_chat=self._adversarial_chat,
                    objective_scorer=self._objective_scorer,
                    memory_labels=self._memory_labels,
                ),
            )

        return attacks
