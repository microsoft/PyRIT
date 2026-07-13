# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from typing import Any

from pyrit.common import apply_defaults
from pyrit.common.path import EXECUTOR_RED_TEAM_PATH, EXECUTOR_SIMULATED_TARGET_PATH
from pyrit.converter import TextJailbreakConverter
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack
from pyrit.models import AttackSeedGroup
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import TrueFalseScorer


class JailbreakTechnique(ScenarioTechnique):
    """
    Technique for jailbreak attacks.

    The SIMPLE technique just sends the jailbroken prompt and records the response. It is meant to
    expose an obvious way of using this scenario without worrying about additional tweaks and changes
    to the prompt.

    COMPLEX techniques use additional techniques to enhance the jailbreak like modifying the
    system prompt or probing the target model for an additional vulnerability (e.g. the SkeletonKeyAttack).
    They are meant to provide a sense of how well a jailbreak generalizes to slight changes in the delivery
    method.
    """

    # Aggregate members (special markers that expand to techniques with matching tags)
    ALL = ("all", {"all"})
    SIMPLE = ("simple", {"simple"})
    COMPLEX = ("complex", {"complex"})

    # Simple techniques
    PromptSending = ("prompt_sending", {"simple"})

    # Complex techniques
    ManyShot = ("many_shot", {"complex"})
    SkeletonKey = ("skeleton", {"complex"})
    RolePlay = ("role_play_persuasion", {"complex"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            set[str]: Set of tags that are aggregate markers.
        """
        # Include base class aggregates ("all") and add scenario-specific ones
        return super().get_aggregate_tags() | {"simple", "complex"}


class Jailbreak(Scenario):
    """
    Jailbreak scenario implementation for PyRIT.

    This scenario tests how vulnerable models are to jailbreak attacks by applying
    various single-turn jailbreak templates to a set of test prompts. The responses are
    scored to determine if the jailbreak was successful.
    """

    VERSION: int = 1

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_harms"]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
        num_templates: int | None = None,
        num_attempts: int = 1,
        jailbreak_names: list[str] | None = None,
    ) -> None:
        """
        Initialize the jailbreak scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for detecting successful jailbreaks
                (non-refusal). If not provided, defaults to an inverted refusal scorer.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
            num_templates (int | None): Choose num_templates random jailbreaks rather than using all of them.
            num_attempts (int | None): Number of times to try each jailbreak.
            jailbreak_names (list[str] | None): List of jailbreak names from the template list under datasets.
                to use.

        Raises:
            ValueError: If both jailbreak_names and num_templates are provided, as random selection
                is incompatible with a predetermined list.
            ValueError: If the jailbreak_names list contains a jailbreak that isn't in the listed
                templates.

        """
        if jailbreak_names is None:
            jailbreak_names = []
        if jailbreak_names and num_templates:
            raise ValueError(
                "Please provide only one of `num_templates` (random selection)"
                " or `jailbreak_names` (specific selection)."
            )

        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        self._num_templates = num_templates
        self._num_attempts = num_attempts
        self._adversarial_target: PromptTarget | None = None

        # Note that num_templates and jailbreak_names are mutually exclusive.
        # If self._num_templates is None, then this returns all discoverable jailbreak templates.
        # If self._num_templates has some value, then all_templates is a subset of all available
        # templates, but jailbreak_names is guaranteed to be [], so diff = {}.
        all_templates = TextJailBreak.get_jailbreak_templates(num_templates=self._num_templates)

        # Example: if jailbreak_names is {'a', 'b', 'c'}, and all_templates is {'b', 'c', 'd'},
        # then diff = {'a'}, which raises the error as 'a' was not discovered in all_templates.
        diff = set(jailbreak_names) - set(all_templates)
        if len(diff) > 0:
            raise ValueError(f"Error: could not find templates `{diff}`!")

        # If jailbreak_names has some value, then `if jailbreak_names` passes, and self._jailbreaks
        # is set to jailbreak_names. Otherwise we use all_templates.
        self._jailbreaks = jailbreak_names if jailbreak_names else all_templates

        super().__init__(
            version=self.VERSION,
            technique_class=JailbreakTechnique,
            default_technique=JailbreakTechnique.SIMPLE,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["airt_harms"], max_dataset_size=4),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _get_or_create_adversarial_target(self) -> PromptTarget:
        """
        Return the shared adversarial target, creating it on first access.

        Reuses a single PromptTarget instance across all role-play attacks
        to avoid repeated client and TLS setup.

        Returns:
            PromptTarget: The shared adversarial target.
        """
        if self._adversarial_target is None:
            self._adversarial_target = get_default_adversarial_target()
        return self._adversarial_target

    async def _get_atomic_attack_from_technique_async(
        self, *, technique: str, jailbreak_template_name: str, seed_groups: list[AttackSeedGroup]
    ) -> AtomicAttack:
        """
        Create an atomic attack for a specific jailbreak template.

        Args:
            technique (str): JailbreakTechnique to use.
            jailbreak_template_name (str): Name of the jailbreak template file.
            seed_groups (list[AttackSeedGroup]): Seed groups the attack draws from.

        Returns:
            AtomicAttack: An atomic attack using the specified jailbreak template.

        Raises:
            ValueError: If scenario is not properly initialized.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        # Create the jailbreak converter
        jailbreak_converter = TextJailbreakConverter(
            jailbreak_template=TextJailBreak(template_file_name=jailbreak_template_name)
        )

        # Create converter configuration
        converter_config = AttackConverterConfig(
            request_converters=ConverterConfiguration.from_converters(converters=[jailbreak_converter])
        )

        attack: ManyShotJailbreakAttack | PromptSendingAttack | SkeletonKeyAttack | None = None
        args: dict[str, Any] = {
            "objective_target": self._objective_target,
            "attack_scoring_config": AttackScoringConfig(objective_scorer=self._objective_scorer),
            "attack_converter_config": converter_config,
        }

        # Extract template name without extension for the atomic attack name
        template_name = Path(jailbreak_template_name).stem

        match technique:
            case "many_shot":
                attack = ManyShotJailbreakAttack(**args)
            case "prompt_sending":
                attack = PromptSendingAttack(**args)
            case "skeleton":
                attack = SkeletonKeyAttack(**args)
            case "role_play_persuasion":
                # Role play is a simulated-conversation technique: an adversarial
                # chat improvises a short persuasion role play, then the objective
                # is delivered to the target with the jailbreak converter applied.
                adversarial_target = self._get_or_create_adversarial_target()
                role_play_technique = AttackTechniqueFactory.with_simulated_conversation(
                    name="role_play_persuasion",
                    adversarial_chat_system_prompt_path=EXECUTOR_RED_TEAM_PATH
                    / "role_play"
                    / "role_play_persuasion.yaml",
                    next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
                    num_turns=2,
                ).create(
                    objective_target=self._objective_target,
                    attack_scoring_config=AttackScoringConfig(objective_scorer=self._objective_scorer),
                    adversarial_chat=adversarial_target,
                    extra_request_converters=ConverterConfiguration.from_converters(converters=[jailbreak_converter]),
                )
                return AtomicAttack(
                    atomic_attack_name=f"jailbreak_{template_name}",
                    attack_technique=role_play_technique,
                    seed_groups=seed_groups,
                    adversarial_chat=adversarial_target,
                    objective_scorer=self._objective_scorer,
                )
            case _:
                raise ValueError(f"Unknown JailbreakTechnique `{technique}`.")

        if not attack:
            raise ValueError(f"Attack cannot be None!")

        return AtomicAttack(
            atomic_attack_name=f"jailbreak_{template_name}",
            attack_technique=AttackTechnique(attack=attack),
            seed_groups=seed_groups,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Generate atomic attacks for each jailbreak template.

        This method creates an atomic attack for each retrieved jailbreak template.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: List of atomic attacks to execute, one per jailbreak template.
        """
        atomic_attacks: list[AtomicAttack] = []

        seed_groups = list(context.seed_groups)
        techniques = {s.value for s in context.scenario_techniques}

        for technique in techniques:
            for template_name in self._jailbreaks:
                for _ in range(self._num_attempts):
                    atomic_attack = await self._get_atomic_attack_from_technique_async(
                        technique=technique, jailbreak_template_name=template_name, seed_groups=seed_groups
                    )
                    atomic_attacks.append(atomic_attack)

        return atomic_attacks
