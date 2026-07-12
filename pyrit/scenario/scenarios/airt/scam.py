# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyrit.common import apply_defaults
from pyrit.common.path import EXECUTOR_RED_TEAM_PATH, EXECUTOR_SIMULATED_TARGET_PATH, SCORER_SEED_PROMPT_PATH
from pyrit.executor.attack import ContextComplianceAttack, RedTeamingAttack
from pyrit.executor.attack.core.attack_config import AttackAdversarialConfig, AttackScoringConfig
from pyrit.models import Parameter, SeedAttackGroup, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_strategy import AttackStrategy

logger = logging.getLogger(__name__)
PERSUASION_DECEPTION_PATH = Path(EXECUTOR_RED_TEAM_PATH, "persuasion_deception").resolve()


class ScamTechnique(ScenarioTechnique):
    """
    Techniques for the Scam Scenario.

    Non-Aggregate Values:
    - ContextCompliance: This single-turn attack attempts to bypass safety measures by rephrasing the objective into
        a more benign context.
        It uses an adversarial chat target to:
        1) rephrase the objective (first user turn)
        2) generate the assistant's response to the benign question (first assistant turn)
        3) rephrase the original objective as a follow-up question (end of first assistant turn)
        This conversation is prepended and sent with an affirmative "yes" to get a response from the target.
    - RolePlay: This technique stages a short simulated role-play conversation (an adversarial chat
        improvises the `role_play_persuasion_written.yaml` written-persuasion persona) and then delivers
        the objective to the target. It is framed in the context of creating written samples to be used
        during training seminars.
    - PersuasiveRedTeamingAttack: This multi-turn attack uses a persuasive persona with the `RedTeamingAttack` to
        iteratively convince the target to comply with the scam objective over multiple turns.

    Aggregate Values:
    - ALL: Every technique (both single-turn and the multi-turn PersuasiveRedTeamingAttack).
    - DEFAULT: The single-turn techniques (ContextCompliance, RolePlay). Excludes the multi-turn
        PersuasiveRedTeamingAttack so the default run stays fast; opt into it via ALL or MULTI_TURN.
    - SINGLE_TURN / MULTI_TURN: Group techniques by turn count. DEFAULT intentionally shares
        membership with SINGLE_TURN today (both are the single-turn techniques); they are kept
        distinct because DEFAULT is the recommended fast default while SINGLE_TURN is a turn-count
        grouping, and their membership may diverge later.
    """

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    MULTI_TURN = ("multi_turn", {"multi_turn"})

    ContextCompliance = ("context_compliance", {"single_turn", "default"})
    RolePlay = ("role_play_persuasion_written", {"single_turn", "default"})
    PersuasiveRedTeamingAttack = ("persuasive_rta", {"multi_turn"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            set[str]: Set of tags that are aggregate markers.
        """
        # Include base class aggregates ("all") and add scenario-specific ones
        return super().get_aggregate_tags() | {"default", "single_turn", "multi_turn"}


class Scam(Scenario):
    """
    Scam scenario evaluates an endpoint's ability to generate scam-related materials
    (e.g., phishing emails, fraudulent messages) with primarily persuasion-oriented techniques.
    """

    VERSION: int = 2

    @classmethod
    def _get_additional_scoring_questions(cls) -> list[Path]:
        """
        Override true/false question paths for scam objective scoring.

        Returns:
            Sequence[Path]: Paths to true/false question paths for scam objective scoring.
        """
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "scams.yaml"]

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_scams"]

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare custom parameters this scenario accepts from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run.
        """
        return [
            Parameter(
                name="max_turns",
                description="Maximum conversation turns for the persuasive_rta technique.",
                param_type=int,
                default=5,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        adversarial_chat: PromptTarget | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the ScamScenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Custom scorer for objective
                evaluation.
            adversarial_chat (PromptTarget | None): Chat target used to rephrase the
                objective into the role-play context (in single-turn techniques).
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        # Used for multiturn techniques and role-play simulated conversations
        self._adversarial_chat = adversarial_chat if adversarial_chat else get_default_adversarial_target()
        self._adversarial_config = AttackAdversarialConfig(target=self._adversarial_chat)

        super().__init__(
            version=self.VERSION,
            technique_class=ScamTechnique,
            default_technique=ScamTechnique.DEFAULT,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["airt_scams"], max_dataset_size=4),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _get_atomic_attack_from_technique(self, *, technique: str, seed_groups: list[SeedAttackGroup]) -> AtomicAttack:
        """
        Translate the techniques into actual AtomicAttacks.

        Args:
            technique (str): The technique to create the attack from.
            seed_groups (list[SeedAttackGroup]): Seed groups the attack draws from.

        Returns:
            AtomicAttack: Configured for the specified technique.

        Raises:
            ValueError: If scenario is not properly initialized or an unknown ScamTechnique is provided.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )
        attack_strategy: AttackStrategy[Any, Any] | None = None

        if technique == "persuasive_rta":
            # Set system prompt to generic persuasion persona
            self._adversarial_config.system_prompt = SeedPrompt.from_yaml_file(
                Path(PERSUASION_DECEPTION_PATH, "persuasion_persona_generic.yaml").resolve()
            )

            attack_strategy = RedTeamingAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
                attack_adversarial_config=self._adversarial_config,
                max_turns=self.params["max_turns"],
            )
        elif technique == "role_play_persuasion_written":
            # Role play is now a simulated-conversation technique: an adversarial
            # chat improvises a short written-persuasion role play, then the
            # objective is delivered to the target.
            role_play_technique = AttackTechniqueFactory.with_simulated_conversation(
                name="role_play_persuasion_written",
                adversarial_chat_system_prompt_path=EXECUTOR_RED_TEAM_PATH
                / "role_play"
                / "role_play_persuasion_written.yaml",
                next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
                num_turns=2,
            ).create(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
                adversarial_chat=self._adversarial_chat,
            )
            return AtomicAttack(
                atomic_attack_name=f"scam_{technique}",
                attack_technique=role_play_technique,
                seed_groups=seed_groups,
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._scorer_config.objective_scorer,
                memory_labels=self._memory_labels,
            )
        elif technique == "context_compliance":
            # Set system prompt to default
            self._adversarial_config.system_prompt = None

            attack_strategy = ContextComplianceAttack(
                objective_target=self._objective_target,
                attack_scoring_config=self._scorer_config,
                attack_adversarial_config=self._adversarial_config,
            )
        else:
            raise ValueError(f"Unknown ScamTechnique: {technique}")

        return AtomicAttack(
            atomic_attack_name=f"scam_{technique}",
            attack_technique=AttackTechnique(attack=attack_strategy),
            seed_groups=seed_groups,
            memory_labels=self._memory_labels,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Generate atomic attacks for each technique.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: List of atomic attacks to execute.
        """
        seed_groups = list(context.seed_groups)
        techniques = {s.value for s in context.scenario_techniques}

        return [
            self._get_atomic_attack_from_technique(technique=technique, seed_groups=seed_groups)
            for technique in techniques
        ]
