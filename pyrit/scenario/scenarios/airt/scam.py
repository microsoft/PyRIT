# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from pathlib import Path
from typing import Any, List, Optional

from pyrit.common import apply_defaults
from pyrit.common.path import (
    EXECUTOR_RED_TEAM_PATH,
    SCORER_SEED_PROMPT_PATH,
)
from pyrit.executor.attack import (
    ContextComplianceAttack,
    RedTeamingAttack,
    RolePlayAttack,
    RolePlayPaths,
)
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.core.attack_strategy import AttackStrategy
from pyrit.models import SeedAttackGroup, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
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

logger = logging.getLogger(__name__)
PERSUASION_DECEPTION_PATH = Path(EXECUTOR_RED_TEAM_PATH, "persuasion_deception").resolve()


class ScamStrategy(ScenarioStrategy):
    """
    Strategies for the Scam Scenario.

    Non-Aggregate Values:
    - ContextCompliance: This single-turn attack attempts to bypass safety measures by rephrasing the objective into
        a more benign context.
        It uses an adversarial chat target to:
        1) rephrase the objective (first user turn)
        2) generate the assistant's response to the benign question (first assistant turn)
        3) rephrase the original objective as a follow-up question (end of first assistant turn)
        This conversation is prepended and sent with an affirmative "yes" to get a response from the target.
    - RolePlay: This single-turn attack uses the `persuasion_script_written.yaml` role-play scenario to convince the
        target to help draft a response to the scam objective. It is framed in the context of creating written samples
        to be used during training seminars.
    - PersuasiveRedTeamingAttack: This multi-turn attack uses a persuasive persona with the `RedTeamingAttack` to
        iteratively convince the target to comply with the scam objective over multiple turns.
    """

    ALL = ("all", {"all"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    MULTI_TURN = ("multi_turn", {"multi_turn"})

    ContextCompliance = ("context_compliance", {"single_turn"})
    RolePlay = ("role_play", {"single_turn"})
    PersuasiveRedTeamingAttack = ("persuasive_rta", {"multi_turn"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            set[str]: Set of tags that are aggregate markers.
        """
        # Include base class aggregates ("all") and add scenario-specific ones
        return super().get_aggregate_tags() | {"single_turn", "multi_turn"}


class Scam(Scenario):
    """
    Scam scenario evaluates an endpoint's ability to generate scam-related materials
    (e.g., phishing emails, fraudulent messages) with primarily persuasion-oriented techniques.
    """

    version: int = 1

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Get the strategy enum class for this scenario.

        Returns:
            Type[ScenarioStrategy]: The ScamStrategy enum class.
        """
        return ScamStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Get the default strategy used when no strategies are specified.

        Returns:
            ScenarioStrategy: ScamStrategy.ALL (all scam strategies).
        """
        return ScamStrategy.ALL

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_scams"]

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        Returns:
            DatasetConfiguration: Configuration with airt_scams dataset.
        """
        return DatasetConfiguration(dataset_names=["airt_scams"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        objectives: Optional[List[str]] = None,
        objective_scorer: Optional[TrueFalseScorer] = None,
        adversarial_chat: Optional[PromptChatTarget] = None,
        include_baseline: bool = True,
        scenario_result_id: Optional[str] = None,
    ) -> None:
        """
        Initialize the ScamScenario.

        Args:
            objectives (Optional[List[str]]): List of objectives to test for scam-related harms.
            objective_scorer (Optional[TrueFalseScorer]): Custom scorer for objective
                evaluation.
            adversarial_chat (Optional[PromptChatTarget]): Chat target used to rephrase the
                objective into the role-play context (in single-turn strategies).
            include_baseline (bool): Whether to include a baseline atomic attack that sends all objectives
                without modifications. Defaults to True. When True, a "baseline" attack is automatically
                added as the first atomic attack, allowing comparison between unmodified prompts and
                encoding-modified prompts.
            scenario_result_id (Optional[str]): Optional ID of an existing scenario result to resume.
        """
        if objectives is not None:
            logger.warning(
                "objectives is deprecated and will be removed in 0.13.0. "
                "Use dataset_config in initialize_async instead."
            )

        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        # Used for multiturn strategies and RolePlayAttack rephrasing
        self._adversarial_chat = adversarial_chat if adversarial_chat else self._get_default_adversarial_target()
        self._adversarial_config = AttackAdversarialConfig(target=self._adversarial_chat)

        super().__init__(
            name="Scam",
            version=self.version,
            strategy_class=ScamStrategy,
            objective_scorer=objective_scorer,
            include_default_baseline=include_baseline,
            scenario_result_id=scenario_result_id,
        )

        # Store deprecated objectives for later resolution in _resolve_seed_groups
        self._deprecated_objectives = objectives
        # Will be resolved in _get_atomic_attacks_async
        self._seed_groups: Optional[List[SeedAttackGroup]] = None

    def _get_default_objective_scorer(self) -> TrueFalseCompositeScorer:
        """
        Retrieve the default objective scorer.
        The scoring mechanism is as follows:
        1. Detect for objective achieved using a SelfAskTrueFalseScorer with a custom true/false question path.
        2. Detect for NON-refusal as a backstop. (If the target model refuses to produce scam materials, we want that
        to register as a negative score).

        Returns:
            TrueFalseCompositeScorer: Default objective scorer with backstop and scam materials evaluation.
        """
        scam_materials = SelfAskTrueFalseScorer(
            chat_target=OpenAIChatTarget(
                endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
                api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
                model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
                temperature=0.9,
            ),
            true_false_question_path=SCORER_SEED_PROMPT_PATH / "true_false_question" / "scams.yaml",
        )

        backstop = TrueFalseInverterScorer(
            scorer=SelfAskRefusalScorer(
                chat_target=OpenAIChatTarget(
                    endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
                    api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
                    model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
                )
            )
        )

        return TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[scam_materials, backstop])

    def _get_default_adversarial_target(self) -> OpenAIChatTarget:
        """
        Provide an OpenAI target for the role-play rephrasing step.

        Returns:
            OpenAIChatTarget: Target that supplies the persuasion script rephrasing.
        """
        return OpenAIChatTarget(
            endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
            api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            temperature=1.2,
        )

    def _resolve_seed_groups(self) -> List[SeedAttackGroup]:
        """
        Resolve seed groups from deprecated objectives or dataset configuration.

        Returns:
            List[SeedAttackGroup]: List of seed attack groups with objectives to be tested.

        Raises:
            ValueError: If both 'objectives' parameter and 'dataset_config' are specified.
        """
        # Check for conflict between deprecated objectives and dataset_config
        if self._deprecated_objectives is not None and self._dataset_config_provided:
            raise ValueError(
                "Cannot specify both 'objectives' parameter and 'dataset_config'. "
                "Please use only 'dataset_config' in initialize_async."
            )

        # Use deprecated objectives if provided
        if self._deprecated_objectives is not None:
            return [SeedAttackGroup(seeds=[SeedObjective(value=obj)]) for obj in self._deprecated_objectives]

        # Use dataset_config (guaranteed to be set by initialize_async)
        seed_groups = self._dataset_config.get_all_seed_attack_groups()

        if not seed_groups:
            self._raise_dataset_exception()

        return list(seed_groups)

    def _get_atomic_attack_from_strategy(self, strategy: str) -> AtomicAttack:
        """
        Translate the strategies into actual AtomicAttacks.

        Args:
            strategy (ScenarioCompositeStrategy): The strategy to create the attack from.

        Returns:
            AtomicAttack: Configured for the specified strategy.

        Raises:
            ValueError: If an unknown ScamStrategy is provided.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        assert self._objective_target is not None
        attack_strategy: Optional[AttackStrategy[Any, Any]] = None

        if strategy == "persuasive_rta":
            # Set system prompt to generic persuasion persona
            self._adversarial_config.system_prompt_path = Path(
                PERSUASION_DECEPTION_PATH, "persuasion_persona_generic.yaml"
            ).resolve()

            attack_strategy = RedTeamingAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
                attack_adversarial_config=self._adversarial_config,
                max_turns=5,
            )
        elif strategy == "role_play":
            attack_strategy = RolePlayAttack(
                objective_target=self._objective_target,
                adversarial_chat=self._adversarial_chat,
                role_play_definition_path=RolePlayPaths.PERSUASION_SCRIPT_WRITTEN.value,
                attack_scoring_config=self._scorer_config,
            )
        elif strategy == "context_compliance":
            # Set system prompt to default
            self._adversarial_config.system_prompt_path = None

            attack_strategy = ContextComplianceAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
                attack_adversarial_config=self._adversarial_config,
            )
        else:
            raise ValueError(f"Unknown ScamStrategy: {strategy}")

        return AtomicAttack(
            atomic_attack_name=f"scam_{strategy}",
            attack=attack_strategy,
            seed_groups=self._seed_groups,
            memory_labels=self._memory_labels,
        )

    async def _get_atomic_attacks_async(self) -> List[AtomicAttack]:
        """
        Generate atomic attacks for each strategy.

        Returns:
            List[AtomicAttack]: List of atomic attacks to execute.
        """
        # Resolve seed groups from deprecated objectives or dataset config
        self._seed_groups = self._resolve_seed_groups()

        atomic_attacks: List[AtomicAttack] = []
        strategies = ScenarioCompositeStrategy.extract_single_strategy_values(
            composites=self._scenario_composites, strategy_type=ScamStrategy
        )

        for strategy in strategies:
            atomic_attacks.append(self._get_atomic_attack_from_strategy(strategy))

        return atomic_attacks
