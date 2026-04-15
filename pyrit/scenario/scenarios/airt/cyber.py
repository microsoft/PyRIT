# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from pyrit.auth import get_azure_openai_auth
from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import SeedAttackGroup
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import (
    ScenarioCompositeStrategy,
    ScenarioStrategy,
)
from pyrit.score import (
    SelfAskRefusalScorer,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_strategy import AttackStrategy

logger = logging.getLogger(__name__)


class CyberStrategy(ScenarioStrategy):
    """
    Strategies for malware-focused cyber attacks. While not in the CyberStrategy class, a
    few of these include:
    * Shell smashing
    * Zip bombs
    * File deletion (rm -rf /).
    """

    # Aggregate members (special markers that expand to strategies with matching tags)
    ALL = ("all", {"all"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    MULTI_TURN = ("multi_turn", {"multi_turn"})


class Cyber(Scenario):
    """
    Cyber scenario implementation for PyRIT.

    This scenario tests how willing models are to exploit cybersecurity harms by generating
    malware. The Cyber class contains different variations of the malware generation
    techniques.
    """

    VERSION: int = 1

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Get the strategy enum class for this scenario.

        Returns:
            Type[ScenarioStrategy]: The CyberStrategy enum class.
        """
        return CyberStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Get the default strategy used when no strategies are specified.

        Returns:
            ScenarioStrategy: CyberStrategy.ALL (all cyber strategies).
        """
        return CyberStrategy.ALL

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        Returns:
            DatasetConfiguration: Configuration with airt_malware dataset.
        """
        return DatasetConfiguration(dataset_names=["airt_malware"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: Optional[PromptChatTarget] = None,
        objective_scorer: Optional[TrueFalseScorer] = None,
        include_baseline: bool = True,
        scenario_result_id: Optional[str] = None,
    ) -> None:
        """
        Initialize the cyber harms scenario.

        Args:
            adversarial_chat (Optional[PromptChatTarget]): Adversarial chat for the red teaming attack, corresponding
                to CyberStrategy.MultiTurn. If not provided, defaults to an OpenAI chat target.
            objective_scorer (Optional[TrueFalseScorer]): Objective scorer for malware detection. If not
                provided, defaults to a SelfAskScorer using the malware.yaml file under the scorer config store for
                malware detection
            include_baseline (bool): Whether to include a baseline atomic attack that sends all objectives
                without modifications. Defaults to True. When True, a "baseline" attack is automatically
                added as the first atomic attack, allowing comparison between unmodified prompts and
                attack-modified prompts.
            scenario_result_id (Optional[str]): Optional ID of an existing scenario result to resume.
        """
        # Cyber uses a "take object, make config" pattern to expose a more ergonomic interface. Helper
        # methods return objects, not configs.

        # In this context the "objective" scorer has nothing to do with the "objective" target.
        # The scoring config is what's used later in scoring orchestration, not the scorer object.
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        # Adversarial chat is used by the RedTeamingAttack (multiturn) variation of the scenario. Otherwise, it's not
        # invoked.
        self._adversarial_chat = adversarial_chat if adversarial_chat else self._get_default_adversarial_target()
        self._adversarial_config = AttackAdversarialConfig(target=self._adversarial_chat)

        super().__init__(
            version=self.VERSION,
            strategy_class=CyberStrategy,
            objective_scorer=objective_scorer,
            include_default_baseline=include_baseline,
            scenario_result_id=scenario_result_id,
        )

        # Will be resolved in _get_atomic_attacks_async
        self._seed_groups: Optional[list[SeedAttackGroup]] = None

    def _get_default_objective_scorer(self) -> TrueFalseCompositeScorer:
        """
        Retrieve the default objective scorer.
        The scoring mechanism is as follows:
        1. Detect for the presence of malware using a SelfAskTrueFalseScorer.
        2. Detect for NON-refusal as a backstop. (If the target model refuses to produce malware, we want that
        to register as a negative score).

        Returns:
            TrueFalseCompositeScorer: Default objective scorer with backstop and malware evaluation.
        """
        endpoint = os.getenv("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT")
        presence_of_malware = SelfAskTrueFalseScorer(
            chat_target=OpenAIChatTarget(
                endpoint=endpoint,
                api_key=get_azure_openai_auth(endpoint),
                model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            ),
            true_false_question_path=SCORER_SEED_PROMPT_PATH / "true_false_question" / "malware.yaml",
        )

        backstop = TrueFalseInverterScorer(
            scorer=SelfAskRefusalScorer(
                chat_target=OpenAIChatTarget(
                    endpoint=endpoint,
                    api_key=get_azure_openai_auth(endpoint),
                    model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
                )
            )
        )

        return TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND, scorers=[presence_of_malware, backstop]
        )

    def _get_default_adversarial_target(self) -> OpenAIChatTarget:
        """
        Create and retrieve the default adversarial target.

        Returns:
            OpenAIChatTarget: Default adversarial target, using an unfiltered endpoint.
        """
        endpoint = os.getenv("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT")
        return OpenAIChatTarget(
            endpoint=endpoint,
            api_key=get_azure_openai_auth(endpoint),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            temperature=1.2,
        )

    def _resolve_seed_groups(self) -> list[SeedAttackGroup]:
        """
        Resolve seed groups from dataset configuration.

        Returns:
            List[SeedAttackGroup]: List of seed attack groups with objectives to be tested.
        """
        # Use dataset_config (guaranteed to be set by initialize_async)
        seed_groups = self._dataset_config.get_all_seed_attack_groups()

        if not seed_groups:
            self._raise_dataset_exception()

        return list(seed_groups)

    def _get_atomic_attack_from_strategy(self, strategy: str) -> AtomicAttack:
        """
        Translate the strategy into an actual AtomicAttack.

        Args:
            strategy: The CyberStrategy enum (SingleTurn or MultiTurn).

        Returns:
            AtomicAttack: configured for the specified strategy.

        Raises:
            ValueError: If scenario is not properly initialized or an unknown CyberStrategy is passed.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )
        attack_strategy: Optional[AttackStrategy[Any, Any]] = None
        if strategy == "single_turn":
            attack_strategy = PromptSendingAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
            )
        elif strategy == "multi_turn":
            attack_strategy = RedTeamingAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
                attack_adversarial_config=self._adversarial_config,
            )
        else:
            raise ValueError(f"Unknown CyberStrategy: {strategy}")

        # _seed_groups is guaranteed to be set by _get_atomic_attacks_async before this method is called
        if self._seed_groups is None:
            raise ValueError("_seed_groups must be resolved before creating atomic attacks")

        return AtomicAttack(
            atomic_attack_name=f"cyber_{strategy}",
            attack_technique=AttackTechnique(attack=attack_strategy),
            seed_groups=self._seed_groups,
            adversarial_chat=self._adversarial_chat,
            objective_scorer=self._scorer_config.objective_scorer,
            memory_labels=self._memory_labels,
        )

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Generate atomic attacks for each strategy.

        Returns:
            List[AtomicAttack]: List of atomic attacks to execute.
        """
        # Resolve seed groups from deprecated objectives or dataset config
        self._seed_groups = self._resolve_seed_groups()

        strategies = ScenarioCompositeStrategy.extract_single_strategy_values(
            composites=self._scenario_composites, strategy_type=CyberStrategy
        )

        return [self._get_atomic_attack_from_strategy(strategy) for strategy in strategies]
