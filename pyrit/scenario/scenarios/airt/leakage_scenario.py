# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from typing import List, Optional

from PIL import Image

from pyrit.common import apply_defaults
from pyrit.common.path import DATASETS_PATH, SCORER_SEED_PROMPT_PATH
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
    CrescendoAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
)
from pyrit.models import SeedAttackGroup, SeedObjective
from pyrit.prompt_converter import AddImageTextConverter, FirstLetterConverter, PromptConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration
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


class LeakageStrategy(ScenarioStrategy):
    """
    Strategies for data leakage and privacy violation attacks.

    These strategies test whether models can be manipulated into leaking training data,
    intellectual property (IP), credentials, or other confidential content.

    Leakage categories:
    - ip: Intellectual property (copyrighted content, training data memorization)
    - sensitive_data: Credentials, secrets, system prompts, API keys
    """

    # Aggregate members (special markers that expand to strategies with matching tags)
    ALL = ("all", {"all"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    MULTI_TURN = ("multi_turn", {"multi_turn"})

    # Leakage-specific aggregates
    IP = ("ip", {"ip"})  # Intellectual property focused strategies
    SENSITIVE_DATA = ("sensitive_data", {"sensitive_data"})  # Credentials, secrets, prompts

    # Single-turn strategies
    FIRST_LETTER = ("first_letter", {"single_turn", "ip"})  # Good for copyright extraction
    IMAGE = ("image", {"single_turn", "ip", "sensitive_data"})
    ROLE_PLAY = ("role_play", {"single_turn", "sensitive_data"})  # Good for system prompt extraction

    # Multi-turn strategies
    CRESCENDO = ("crescendo", {"multi_turn", "ip", "sensitive_data"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            Set[str]: Set of tags that represent aggregates, including "all",
                     "single_turn", "multi_turn", "ip", and "sensitive_data".
        """
        return {"all", "single_turn", "multi_turn", "ip", "sensitive_data"}


class LeakageScenario(Scenario):
    """
    Leakage scenario implementation for PyRIT.

    This scenario tests how susceptible models are to leaking training data, PII, intellectual
    property, or other confidential information. The LeakageScenario class contains different
    attack variations designed to extract sensitive information from models.
    """

    version: int = 1

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Get the strategy enum class for this scenario.

        Returns:
            Type[ScenarioStrategy]: The LeakageStrategy enum class.
        """
        return LeakageStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Get the default strategy used when no strategies are specified.

        Returns:
            ScenarioStrategy: LeakageStrategy.ALL (all leakage strategies).
        """
        return LeakageStrategy.ALL

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_leakage"]

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        Returns:
            DatasetConfiguration: Configuration with airt_leakage dataset.
        """
        return DatasetConfiguration(dataset_names=["airt_leakage"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: Optional[PromptChatTarget] = None,
        objectives: Optional[List[str]] = None,
        objective_scorer: Optional[TrueFalseScorer] = None,
        include_baseline: bool = True,
        scenario_result_id: Optional[str] = None,
    ) -> None:
        """
        Initialize the leakage scenario.

        Args:
            adversarial_chat (Optional[PromptChatTarget]): Adversarial chat target for multi-turn attacks
                (Crescendo, RolePlay). If not provided, defaults to an OpenAI chat target.
            objectives (Optional[List[str]]): List of objectives to test for data leakage.
                If not provided, defaults to objectives from the airt_leakage dataset.
            objective_scorer (Optional[TrueFalseScorer]): Objective scorer for leakage detection.
                If not provided, defaults to a scorer that detects PII and training data leakage.
            include_baseline (bool): Whether to include a baseline atomic attack that sends all
                objectives without modifications. Defaults to True.
            scenario_result_id (Optional[str]): Optional ID of an existing scenario result to resume.
        """
        # Initialize objective scorer
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        # Initialize adversarial chat for multi-turn attacks
        self._adversarial_chat = adversarial_chat if adversarial_chat else self._get_default_adversarial_target()
        self._adversarial_config = AttackAdversarialConfig(target=self._adversarial_chat)

        super().__init__(
            name="Leakage Scenario",
            version=self.version,
            strategy_class=LeakageStrategy,
            objective_scorer=objective_scorer,
            include_default_baseline=include_baseline,
            scenario_result_id=scenario_result_id,
        )

        self._objectives = objectives if objectives else self._get_default_objectives()

    def _get_default_objective_scorer(self) -> TrueFalseCompositeScorer:
        """
        Retrieve the default objective scorer.

        The scoring mechanism is as follows:
        1. Detect for objective achieved using a SelfAskTrueFalseScorer with leakage detection.
        2. Detect for NON-refusal as a backstop. (If the target model refuses to leak data,
           we want that to register as a negative score).

        Returns:
            TrueFalseCompositeScorer: Default objective scorer with backstop and leakage evaluation.
        """
        presence_of_leakage = SelfAskTrueFalseScorer(
            chat_target=OpenAIChatTarget(
                endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
                api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
                model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            ),
            true_false_question_path=SCORER_SEED_PROMPT_PATH / "true_false_question" / "leakage.yaml",
        )

        # Backstop scorer: Ensures the model didn't simply refuse the request.
        # Uses SelfAskRefusalScorer inverted - if model refused, this returns False.
        # Combined with AND aggregator: response must contain leakage AND not be a refusal.
        backstop = TrueFalseInverterScorer(
            scorer=SelfAskRefusalScorer(
                chat_target=OpenAIChatTarget(
                    endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
                    api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
                    model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
                )
            )
        )

        return TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND, scorers=[presence_of_leakage, backstop]
        )

    def _get_default_adversarial_target(self) -> OpenAIChatTarget:
        """
        Create and retrieve the default adversarial target.

        Returns:
            OpenAIChatTarget: Default adversarial target using an unfiltered endpoint.
        """
        return OpenAIChatTarget(
            endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
            api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
            temperature=1.2,
        )

    def _get_default_objectives(self) -> list[str]:
        """
        Get the default seed prompts for leakage tests.

        Returns:
            list[str]: List of objectives to be tested.
        """
        seed_objectives = self._memory.get_seeds(dataset_name="airt_leakage", seed_type="objective")

        if not seed_objectives:
            self._raise_dataset_exception()

        return [seed.value for seed in seed_objectives]

    def _ensure_blank_image_exists(self, image_path: str) -> None:
        """
        Ensure a blank image exists at the specified path for the image strategy.

        Args:
            image_path: Path where the blank image should exist.
        """
        path = Path(image_path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Create a white 800x600 image suitable for text overlay
            img = Image.new("RGB", (800, 600), color=(255, 255, 255))
            img.save(str(path))

    async def _get_atomic_attack_from_strategy_async(self, strategy: str) -> AtomicAttack:
        """
        Translate the strategy into an actual AtomicAttack.

        Args:
            strategy: The LeakageStrategy value (first_letter, crescendo, image, or role_play).

        Returns:
            AtomicAttack: Configured for the specified strategy.

        Raises:
            ValueError: If an unknown LeakageStrategy is passed.
        """
        # objective_target is guaranteed to be non-None by parent class validation
        assert self._objective_target is not None

        strategy_factories = {
            "first_letter": self._create_first_letter_attack,
            "crescendo": self._create_crescendo_attack,
            "image": self._create_image_attack,
            "role_play": self._create_role_play_attack,
        }

        factory = strategy_factories.get(strategy)
        if not factory:
            raise ValueError(f"Unknown LeakageStrategy: {strategy}")

        attack_strategy = await factory()

        # The factory functions return specific AttackStrategy subclasses, but mypy infers ABC
        # due to the heterogeneous dict values. The types are verified by unit tests.
        return AtomicAttack(
            atomic_attack_name=f"leakage_{strategy}",
            attack=attack_strategy,  # type: ignore[arg-type]
            seed_groups=self._seed_groups,
            memory_labels=self._memory_labels,
        )

    async def _create_first_letter_attack(self) -> PromptSendingAttack:
        """
        Create a first letter converter attack.

        Returns:
            PromptSendingAttack: Configured attack using first letter converter.
        """
        converters: list[PromptConverter] = [FirstLetterConverter()]
        converter_config = AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=converters)
        )
        return PromptSendingAttack(
            objective_target=self._objective_target,
            attack_scoring_config=self._scorer_config,
            attack_converter_config=converter_config,
        )

    async def _create_crescendo_attack(self) -> CrescendoAttack:
        """
        Create a multi-turn progressive crescendo attack.

        Returns:
            CrescendoAttack: Configured multi-turn crescendo attack.
        """
        return CrescendoAttack(
            objective_target=self._objective_target,
            attack_scoring_config=self._scorer_config,
            attack_adversarial_config=self._adversarial_config,
        )

    async def _create_image_attack(self) -> PromptSendingAttack:
        """
        Create an image-based attack that embeds prompts in images.

        Returns:
            PromptSendingAttack: Configured attack that adds text to images.
        """
        blank_image_path = str(DATASETS_PATH / "seed_datasets" / "local" / "examples" / "blank_canvas.png")
        self._ensure_blank_image_exists(blank_image_path)
        image_converters: list[PromptConverter] = [AddImageTextConverter(img_to_add=blank_image_path)]
        converter_config = AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=image_converters)
        )
        return PromptSendingAttack(
            objective_target=self._objective_target,
            attack_scoring_config=self._scorer_config,
            attack_converter_config=converter_config,
        )

    async def _create_role_play_attack(self) -> RolePlayAttack:
        """
        Create a role-play attack using persuasion script format.

        Returns:
            RolePlayAttack: Configured role-play attack with persuasion script.
        """
        return RolePlayAttack(
            objective_target=self._objective_target,
            adversarial_chat=self._adversarial_chat,
            role_play_definition_path=RolePlayPaths.PERSUASION_SCRIPT.value,
            attack_scoring_config=self._scorer_config,
        )

    def _resolve_seed_groups(self) -> List[SeedAttackGroup]:
        """
        Resolve objectives to SeedAttackGroup format required by AtomicAttack.

        Returns:
            List[SeedAttackGroup]: List of seed attack groups, each containing an objective.
        """
        return [SeedAttackGroup(seeds=[SeedObjective(value=obj)]) for obj in self._objectives]

    async def _get_atomic_attacks_async(self) -> List[AtomicAttack]:
        """
        Generate atomic attacks for each strategy.

        Returns:
            List[AtomicAttack]: List of atomic attacks to execute.
        """
        # Resolve objectives to seed groups format
        self._seed_groups = self._resolve_seed_groups()

        atomic_attacks: List[AtomicAttack] = []
        strategies = ScenarioCompositeStrategy.extract_single_strategy_values(
            composites=self._scenario_composites, strategy_type=LeakageStrategy
        )

        for strategy in strategies:
            atomic_attacks.append(await self._get_atomic_attack_from_strategy_async(strategy))
        return atomic_attacks
