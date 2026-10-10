# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import random
from functools import cache
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pyrit.common import apply_defaults
from pyrit.common.path import DATASETS_PATH
from pyrit.converter import RandomTranslationConverter, TranslationConverter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import (
    BoundedDatasetSize,
    Parameter,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateStatus,
    ScenarioRunSizeFactor,
    SeedDataset,
)
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import (
    AtomicAttack,
    AttackTechniqueFactory,
    BaselineAttackPolicy,
    DatasetAttackConfiguration,
    Scenario,
    ScenarioTechnique,
    get_default_adversarial_target,
)
from pyrit.scenario.core.dataset_configuration import DatasetSource
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    build_baseline_atomic_attack,
    resolve_technique_factories,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

# Metadata key under which the resolved languages are persisted, so a resumed run
# replays the exact same set even when a random sample was drawn.
_LANGUAGES_METADATA_KEY = "languages"

# How many languages a bare run draws at random. Languages multiply against objectives and
# techniques for fixed translation. Override per run with
# ``num_languages`` (random count) or ``languages`` (an explicit set).
_DEFAULT_NUM_LANGUAGES = 5

_PROMPT_SENDING = "prompt_sending"
_TRANSLATION = "translation"
_RANDOM_TRANSLATION = "random_translation"
TranslationStrategy = Literal["translation", "random_translation"]


def _normalize_languages(languages: list[str]) -> list[str]:
    """
    Normalize and deduplicate language names while preserving their first spelling.

    Args:
        languages (list[str]): Language names to normalize.

    Returns:
        list[str]: Unique normalized language names.

    Raises:
        ValueError: If a language name is empty after normalization.
    """
    normalized_by_key: dict[str, str] = {}
    for language in languages:
        normalized = " ".join(language.replace("_", " ").split())
        if not normalized:
            raise ValueError("languages must not contain empty language names.")
        normalized_by_key.setdefault(normalized.casefold(), normalized)
    return list(normalized_by_key.values())


def _language_key(language: str) -> str:
    """
    Build the normalized language key used in atomic attack names.

    Args:
        language (str): A normalized language name.

    Returns:
        str: The lowercase key with spaces replaced by underscores.
    """
    return language.casefold().replace(" ", "_")


@cache
def _prompt_sending_factory() -> AttackTechniqueFactory:
    """
    Build the scenario-local bare prompt-sending technique factory.

    Returns:
        AttackTechniqueFactory: The prompt-sending factory.
    """
    return AttackTechniqueFactory(
        name=_PROMPT_SENDING,
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
    )


def _extra_default_factories() -> dict[str, AttackTechniqueFactory]:
    """Return scenario-local technique factories keyed by name."""
    return {_PROMPT_SENDING: _prompt_sending_factory()}


@cache
def _build_multilingual_technique() -> type[ScenarioTechnique]:
    """
    Build the Multilingual technique class from text-compatible registered factories.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = [
        factory
        for factory in list(registry.get_factories_or_raise().values()) + list(_extra_default_factories().values())
        if factory.can_append_request_converter(converter_type=TranslationConverter)
    ]
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="MultilingualTechnique",
        factories=factories,
        default_names={_PROMPT_SENDING},
    )


class Multilingual(Scenario):
    """
    Multilingual scenario implementation for PyRIT.

    Tests how vulnerable a model is to non-English language use.
    """

    VERSION: int = 1
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    # Default language list
    _DEFAULT_LANGUAGES_SEED_PROMPT_PATH = DATASETS_PATH / "lexicons" / "languages_most_spoken.yaml"

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["harmbench"]

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the run-configurable parameters this scenario accepts (CLI / config file).

        Returns:
            list[Parameter]: The language selectors and translation strategy selector.
        """
        return [
            Parameter(
                name="num_languages",
                description="Draw this many random languages. Mutually exclusive with languages.",
                param_type=int,
                default=None,
            ),
            Parameter(
                name="languages",
                description=(
                    "Explicit languages to use (e.g. French, German, Spanish). "
                    "When omitted, a random sample is drawn. Mutually exclusive with num_languages."
                ),
                param_type=list[str],
                default=None,
            ),
            Parameter(
                name="translation_strategies",
                description=(
                    "Translation strategies to run: translation translates the complete objective into each "
                    "selected language; random_translation translates words using the selected language pool."
                ),
                param_type=list[TranslationStrategy],
                default=[_TRANSLATION, _RANDOM_TRANSLATION],
            ),
        ]

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare supported inputs, excluding user-supplied technique converters.

        Returns:
            list[Parameter]: The supported scenario parameters.
        """
        return [parameter for parameter in super().supported_parameters() if parameter.name != "technique_converters"]

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget | None = None,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the multilingual scenario.

        Args:
            adversarial_chat (PromptTarget | None): Target used by the translation converters.
            objective_scorer (TrueFalseScorer | None): Scorer used to evaluate target responses.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        self._adversarial_chat = adversarial_chat
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )
        self._default_languages = self._get_default_languages()
        self._resolved_languages: list[str] = []

        technique_class = _build_multilingual_technique()

        super().__init__(
            version=self.VERSION,
            uses_default_adversarial_target=adversarial_chat is None,
            technique_class=technique_class,
            default_dataset_config=DatasetAttackConfiguration(
                sources=[DatasetSource(name=name) for name in ["harmbench"]], max_per_dataset="all", max_total=5
            ),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @classmethod
    def _get_default_languages(cls) -> list[str]:
        """
        Load the default languages from the public PyRIT lexicon.

        Returns:
            list[str]: The list of most-spoken languages.
        """
        dataset = SeedDataset.from_yaml_file(cls._DEFAULT_LANGUAGES_SEED_PROMPT_PATH)
        return [str(seed.value) for seed in dataset.seeds]

    async def _resolve_languages_async(self) -> list[str]:
        """
        Resolve the languages for this run, replaying the persisted set on resume.

        On a fresh run this reads the run parameters: an explicit ``languages`` set or a random
        ``num_languages`` sample (defaulting to a small random draw when neither is given). On resume
        the originally chosen set is read back from the stored ``ScenarioResult`` metadata so a random
        sample isn't redrawn (which would diverge from the persisted attacks).

        Returns:
            list[str]: The explicit or randomly sampled languages for this run.

        Raises:
            ValueError: If both ``num_languages`` and ``languages`` are provided,
            or if ``num_languages`` is out of bounds.
        """
        if self._scenario_result_id is not None:
            stored = await self._memory.get_scenario_results_async(scenario_result_ids=[self._scenario_result_id])
            if stored:
                persisted = (stored[0].metadata or {}).get(_LANGUAGES_METADATA_KEY)
                if persisted:
                    return _normalize_languages(list(persisted))

        selection = self._get_language_selection()
        if isinstance(selection, list):
            return selection
        return _normalize_languages(random.sample(self._default_languages, selection))

    def _get_language_selection(self) -> list[str] | int:
        """
        Read the language parameters for a fresh run.

        Returns:
            list[str] | int: The explicit normalized languages, or how many to draw at random.

        Raises:
            ValueError: If both ``num_languages`` and ``languages`` are provided,
            or if ``num_languages`` is out of bounds.
        """
        num_languages = self.params.get("num_languages")
        languages = self.params.get("languages")

        if num_languages is not None and languages is not None:
            raise ValueError(
                "Please provide only one of `num_languages` (random selection) or `languages` (specific selection)."
            )

        if languages is not None:
            if not languages:
                raise ValueError("languages must contain at least one language.")
            return _normalize_languages(languages)

        count = int(num_languages) if num_languages is not None else _DEFAULT_NUM_LANGUAGES
        if count < 1 or count > len(self._default_languages):
            raise ValueError(f"num_languages must be between 1 and {len(self._default_languages)}.")
        return count

    async def _estimate_run_size_async(self, *, budget: BoundedDatasetSize) -> ScenarioRunSizeEstimate:
        """
        Estimate the technique sweep across the language and translation-strategy axes.

        Args:
            budget: Resolved logical-group size contract before technique expansion.

        Returns:
            ScenarioRunSizeEstimate: Baseline plus one slice per selected language and strategy.
        """
        seed_group_count, datasets = await self._get_dataset_size_for_estimate_async(budget=budget)
        selection = self._get_language_selection()
        language_count = len(selection) if isinstance(selection, list) else selection
        strategies = set(self.params.get("translation_strategies") or [_TRANSLATION, _RANDOM_TRANSLATION])
        technique_count = len(self._scenario_techniques)
        sweep_factors = [
            ScenarioRunSizeFactor(label="selected seed-group estimate", count=seed_group_count),
            ScenarioRunSizeFactor(label="selected concrete techniques", count=technique_count),
        ]

        components: list[ScenarioRunSizeComponent] = []
        if self._include_baseline:
            components.append(
                ScenarioRunSizeComponent(
                    label="Baseline",
                    count=seed_group_count,
                    factors=[ScenarioRunSizeFactor(label="selected logical seed groups", count=seed_group_count)],
                    is_baseline=True,
                    note="One unmodified prompt-sending unit per selected seed group.",
                )
            )
        if _TRANSLATION in strategies:
            components.append(
                ScenarioRunSizeComponent(
                    label="Translation",
                    count=seed_group_count * technique_count * language_count,
                    factors=[*sweep_factors, ScenarioRunSizeFactor(label="languages", count=language_count)],
                    note="Each objective is translated into every selected language.",
                )
            )
        if _RANDOM_TRANSLATION in strategies:
            components.append(
                ScenarioRunSizeComponent(
                    label="Random translation",
                    count=seed_group_count * technique_count,
                    factors=sweep_factors,
                    note="One slice that mixes words from the selected languages.",
                )
            )

        return ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Approximate,
            total_attack_count=sum(component.count for component in components),
            components=components,
            datasets=datasets,
            note="Counts planned outer execution units; retries and internal attack turns are excluded.",
        )

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Persist the resolved languages alongside the base scenario metadata.

        Returns:
            dict[str, Any]: The base metadata plus the resolved language set.
        """
        metadata = super()._build_initial_scenario_metadata()
        metadata[_LANGUAGES_METADATA_KEY] = list(self._resolved_languages)
        return metadata

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the technique x dataset x translation-strategy/language attack matrix.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The atomic attacks to execute.

        Raises:
            ValueError: If the scenario is not properly initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        self._resolved_languages = await self._resolve_languages_async()
        adversarial_chat = self._adversarial_chat or get_default_adversarial_target()
        strategies = set(self.params.get("translation_strategies") or [_TRANSLATION, _RANDOM_TRANSLATION])
        technique_factories = resolve_technique_factories(
            context=context,
            extra_factories=_extra_default_factories(),
        )
        builder = MatrixAtomicAttackBuilder(
            objective_target=context.objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
        )

        atomic_attacks: list[AtomicAttack] = []
        if context.include_baseline:
            atomic_attacks.append(
                build_baseline_atomic_attack(
                    objective_target=context.objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=list(context.seed_groups),
                    memory_labels=context.memory_labels,
                )
            )

        if _TRANSLATION in strategies:
            for language in self._resolved_languages:
                converter = TranslationConverter(converter_target=adversarial_chat, language=language)
                atomic_attacks.extend(
                    builder.build(
                        technique_factories=technique_factories,
                        dataset_groups=context.seed_groups_by_dataset,
                        technique_converters={name: [converter] for name in technique_factories},
                        name_fn=lambda combo, language=language: (
                            f"{combo.technique_name}_{_TRANSLATION}_{_language_key(language)}_{combo.dataset_name}"
                        ),
                        display_group_fn=lambda combo, language=language: language,
                        include_baseline=False,
                    )
                )

        if _RANDOM_TRANSLATION in strategies:
            converter = RandomTranslationConverter(
                converter_target=adversarial_chat,
                languages=self._resolved_languages,
            )
            atomic_attacks.extend(
                builder.build(
                    technique_factories=technique_factories,
                    dataset_groups=context.seed_groups_by_dataset,
                    technique_converters={name: [converter] for name in technique_factories},
                    name_fn=lambda combo: f"{combo.technique_name}_{_RANDOM_TRANSLATION}_{combo.dataset_name}",
                    display_group_fn=lambda combo: "Random Translation",
                    include_baseline=False,
                )
            )

        return atomic_attacks
