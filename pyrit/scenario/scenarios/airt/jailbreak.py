# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from typing import Optional, Union

from pyrit.auth import get_azure_openai_auth
from pyrit.common import apply_defaults
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.core.attack_config import (
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.role_play import RolePlayAttack, RolePlayPaths
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack
from pyrit.models import SeedAttackGroup
from pyrit.prompt_converter import TextJailbreakConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioCompositeStrategy, ScenarioStrategy
from pyrit.score import (
    TrueFalseScorer,
)


class JailbreakStrategy(ScenarioStrategy):
    """
    Strategy for jailbreak attacks.

    The SIMPLE strategy just sends the jailbroken prompt and records the response. It is meant to
    expose an obvious way of using this scenario without worrying about additional tweaks and changes
    to the prompt.

    COMPLEX strategies use additional techniques to enhance the jailbreak like modifying the
    system prompt or probing the target model for an additional vulnerability (e.g. the SkeletonKeyAttack).
    They are meant to provide a sense of how well a jailbreak generalizes to slight changes in the delivery
    method.
    """

    # Aggregate members (special markers that expand to strategies with matching tags)
    ALL = ("all", {"all"})
    SIMPLE = ("simple", {"simple"})
    COMPLEX = ("complex", {"complex"})

    # Simple strategies
    PromptSending = ("prompt_sending", {"simple"})

    # Complex strategies
    ManyShot = ("many_shot", {"complex"})
    SkeletonKey = ("skeleton", {"complex"})
    RolePlay = ("role_play", {"complex"})

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
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Get the strategy enum class for this scenario.

        Returns:
            type[ScenarioStrategy]: The JailbreakStrategy enum class.
        """
        return JailbreakStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Get the default strategy used when no strategies are specified.

        Returns:
            ScenarioStrategy: JailbreakStrategy.PromptSending.
        """
        return JailbreakStrategy.SIMPLE

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_harms"]

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        Returns:
            DatasetConfiguration: Configuration with airt_harms dataset.
        """
        return DatasetConfiguration(dataset_names=["airt_harms"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: Optional[TrueFalseScorer] = None,
        include_baseline: bool = False,
        scenario_result_id: Optional[str] = None,
        num_templates: Optional[int] = None,
        num_attempts: int = 1,
        jailbreak_names: list[str] = None,
    ) -> None:
        """
        Initialize the jailbreak scenario.

        Args:
            objective_scorer (Optional[TrueFalseScorer]): Scorer for detecting successful jailbreaks
                (non-refusal). If not provided, defaults to an inverted refusal scorer.
            include_baseline (bool): Whether to include a baseline atomic attack that sends all
                objectives without modifications. Defaults to True.
            scenario_result_id (Optional[str]): Optional ID of an existing scenario result to resume.
            num_templates (Optional[int]): Choose num_templates random jailbreaks rather than using all of them.
            num_attempts (Optional[int]): Number of times to try each jailbreak.
            jailbreak_names (Optional[List[str]]): List of jailbreak names from the template list under datasets.
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
        self._adversarial_target: Optional[OpenAIChatTarget] = None

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
            strategy_class=JailbreakStrategy,
            objective_scorer=self._objective_scorer,
            include_default_baseline=include_baseline,
            scenario_result_id=scenario_result_id,
        )

        # Will be resolved in _get_atomic_attacks_async
        self._seed_groups: Optional[list[SeedAttackGroup]] = None

    def _create_adversarial_target(self) -> OpenAIChatTarget:
        """
        Create a new adversarial target instance.

        Returns:
            OpenAIChatTarget: A fresh adversarial target using an unfiltered endpoint.
        """
        endpoint = os.getenv("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT")
        return OpenAIChatTarget(
            endpoint=endpoint,
            api_key=get_azure_openai_auth(endpoint),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            temperature=1.2,
        )

    def _get_or_create_adversarial_target(self) -> OpenAIChatTarget:
        """
        Return the shared adversarial target, creating it on first access.

        Reuses a single OpenAIChatTarget instance across all role-play attacks
        to avoid repeated client and TLS setup.

        Returns:
            OpenAIChatTarget: The shared adversarial target.
        """
        if self._adversarial_target is None:
            self._adversarial_target = self._create_adversarial_target()
        return self._adversarial_target

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

    async def _get_atomic_attack_from_strategy_async(
        self, *, strategy: str, jailbreak_template_name: str
    ) -> AtomicAttack:
        """
        Create an atomic attack for a specific jailbreak template.

        Args:
            strategy (str): JailbreakStrategy to use.
            jailbreak_template_name (str): Name of the jailbreak template file.

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
            request_converters=PromptConverterConfiguration.from_converters(converters=[jailbreak_converter])
        )

        attack: Optional[Union[ManyShotJailbreakAttack, PromptSendingAttack, RolePlayAttack, SkeletonKeyAttack]] = None
        args = {
            "objective_target": self._objective_target,
            "attack_scoring_config": AttackScoringConfig(objective_scorer=self._objective_scorer),
            "attack_converter_config": converter_config,
        }
        match strategy:
            case "many_shot":
                attack = ManyShotJailbreakAttack(**args)
            case "prompt_sending":
                attack = PromptSendingAttack(**args)
            case "skeleton":
                attack = SkeletonKeyAttack(**args)
            case "role_play":
                args["adversarial_chat"] = self._get_or_create_adversarial_target()
                args["role_play_definition_path"] = RolePlayPaths.PERSUASION_SCRIPT.value
                attack = RolePlayAttack(**args)
            case _:
                raise ValueError(f"Unknown JailbreakStrategy `{strategy}`.")

        if not attack:
            raise ValueError(f"Attack cannot be None!")

        # Extract template name without extension for the atomic attack name
        template_name = Path(jailbreak_template_name).stem

        return AtomicAttack(
            atomic_attack_name=f"jailbreak_{template_name}",
            attack_technique=AttackTechnique(attack=attack),
            seed_groups=self._seed_groups,
        )

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Generate atomic attacks for each jailbreak template.

        This method creates an atomic attack for each retrieved jailbreak template.

        Returns:
            List[AtomicAttack]: List of atomic attacks to execute, one per jailbreak template.
        """
        atomic_attacks: list[AtomicAttack] = []

        # Retrieve seed prompts based on selected strategies
        self._seed_groups = self._resolve_seed_groups()

        strategies = ScenarioCompositeStrategy.extract_single_strategy_values(
            composites=self._scenario_composites, strategy_type=JailbreakStrategy
        )

        for strategy in strategies:
            for template_name in self._jailbreaks:
                for _ in range(self._num_attempts):
                    atomic_attack = await self._get_atomic_attack_from_strategy_async(
                        strategy=strategy, jailbreak_template_name=template_name
                    )
                    atomic_attacks.append(atomic_attack)

        return atomic_attacks
