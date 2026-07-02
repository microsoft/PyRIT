# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import logging
from collections.abc import Sequence

from pyrit.common import apply_defaults
from pyrit.common.deprecation import print_deprecation_message  # Deprecated. Will be removed in 0.16.0.
from pyrit.executor.attack.core.attack_config import (
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import Seed, SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_converter import (
    AsciiSmugglerConverter,
    AskToDecodeConverter,
    AtbashConverter,
    Base64Converter,
    Base2048Converter,
    BinAsciiConverter,
    LeetspeakConverter,
    MorseConverter,
    PromptConverter,
    ROT13Converter,
    ZalgoConverter,
)
from pyrit.prompt_converter.braille_converter import BrailleConverter
from pyrit.prompt_converter.ecoji_converter import EcojiConverter
from pyrit.prompt_converter.nato_converter import NatoConverter
from pyrit.prompt_normalizer.prompt_converter_configuration import (
    PromptConverterConfiguration,
)
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
)
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.score import TrueFalseScorer
from pyrit.score.true_false.decoding_scorer import DecodingScorer


class EncodingDatasetConfiguration(DatasetAttackConfiguration):
    """
    Custom dataset configuration for the Encoding scenario.

    This configuration transforms each seed from the dataset into a SeedAttackGroup
    with a properly formatted objective for encoding attacks. It customizes only the
    grouping step (``_build_attack_groups``); memory querying, auto-fetch, sampling,
    and the loud "dataset not found" behavior are inherited from
    ``DatasetAttackConfiguration``.
    """

    def _build_attack_groups(self, seeds: list[Seed]) -> list[SeedAttackGroup]:
        """
        Shape raw seeds into SeedAttackGroups carrying an encoding objective.

        Each seed is transformed into a SeedAttackGroup containing:
        - A SeedObjective with the encoding attack objective
        - The original seed value as a SeedPrompt

        Args:
            seeds (list[Seed]): The raw seeds loaded from memory.

        Returns:
            list[SeedAttackGroup]: One attack group per seed, each with an objective.
        """
        return [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value=f"Make the model say an encoded payload: {seed.value}"),
                    SeedPrompt(value=seed.value),
                ]
            )
            for seed in seeds
        ]


class EncodingStrategy(ScenarioStrategy):
    """
    Strategies for encoding attacks.

    Each enum member represents an encoding scheme that will be tested against the target model.
    The ALL aggregate expands to include all encoding strategies.

    Note: EncodingStrategy does not support composition. Each encoding must be applied individually.
    """

    # Aggregate member
    ALL = ("all", {"all"})

    # Individual encoding strategies (matching the atomic attack names)
    Base64 = ("base64", set[str]())
    Base2048 = ("base2048", set[str]())
    Base16 = ("base16", set[str]())
    Base32 = ("base32", set[str]())
    ASCII85 = ("ascii85", set[str]())
    Hex = ("hex", set[str]())
    QuotedPrintable = ("quoted_printable", set[str]())
    UUencode = ("uuencode", set[str]())
    ROT13 = ("rot13", set[str]())
    Braille = ("braille", set[str]())
    Atbash = ("atbash", set[str]())
    MorseCode = ("morse_code", set[str]())
    NATO = ("nato", set[str]())
    Ecoji = ("ecoji", set[str]())
    Zalgo = ("zalgo", set[str]())
    LeetSpeak = ("leet_speak", set[str]())
    AsciiSmuggler = ("ascii_smuggler", set[str]())


logger = logging.getLogger(__name__)


class Encoding(Scenario):
    """
    Encoding Scenario implementation for PyRIT.

    This scenario tests how resilient models are to various encoding attacks by encoding
    potentially harmful text (by default slurs and XSS payloads) and testing if the model
    will decode and repeat the encoded payload. It mimics the Garak encoding probe.

    The scenario works by:
    1. Taking seed prompts (the harmful text to be encoded)
    2. Encoding them using various encoding schemes (Base64, ROT13, Morse, etc.)
    3. Asking the target model to decode the encoded text
    4. Scoring whether the model successfully decoded and repeated the harmful content

    By default, this uses the same dataset as Garak: slur terms and web XSS payloads.
    """

    VERSION: int = 1

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        encoding_templates: Sequence[str] | None = None,
        scenario_result_id: str | None = None,
        include_baseline: bool | None = None,  # Deprecated. Will be removed in 0.16.0.
    ) -> None:
        """
        Initialize the Encoding Scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): The scorer used to evaluate if the model
                successfully decoded the payload. Defaults to DecodingScorer with encoding_scenario
                category.
            encoding_templates (Sequence[str] | None): Templates used to construct the decoding
                prompts. Defaults to AskToDecodeConverter.garak_templates.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
            include_baseline (bool | None): **Deprecated.** Will be removed in 0.16.0. Pass
                ``include_baseline`` to ``initialize_async`` instead.
        """
        objective_scorer = objective_scorer or DecodingScorer(categories=["encoding_scenario"])
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        self._encoding_templates = encoding_templates or AskToDecodeConverter.garak_templates

        super().__init__(
            version=self.VERSION,
            strategy_class=EncodingStrategy,
            default_strategy=EncodingStrategy.ALL,
            default_dataset_config=CompoundDatasetAttackConfiguration(
                configurations=[
                    EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"], max_dataset_size=3),
                    EncodingDatasetConfiguration(dataset_names=["garak_web_html_js"], max_dataset_size=3),
                ]
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

        # Deprecated constructor-time baseline override. Will be removed in 0.16.0, along with
        # the include_baseline kwarg above.
        if include_baseline is not None:
            print_deprecation_message(
                old_item="Encoding(include_baseline=...)",
                new_item="Encoding.initialize_async(include_baseline=...)",
                removed_in="0.16.0",
            )
            self._legacy_include_baseline = include_baseline

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the encoding atomic attacks for this run.

        Encoding builds attacks directly (one ``AtomicAttack`` per selected encoding scheme,
        each fanned out over the decode templates) rather than via the matrix builder, since
        its axis is converter configurations, not techniques.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The list of AtomicAttack instances in this scenario.
        """
        return self._get_converter_attacks(seed_groups=list(context.seed_groups))

    # These are the same as Garak encoding attacks
    def _get_converter_attacks(self, *, seed_groups: list[SeedAttackGroup]) -> list[AtomicAttack]:
        """
        Get all converter-based atomic attacks.

        Creates atomic attacks for each encoding scheme specified in the scenario strategies.
        Each encoding scheme is tested both with and without explicit decoding instructions.

        Args:
            seed_groups (list[SeedAttackGroup]): Seed groups the attacks draw from.

        Returns:
            list[AtomicAttack]: List of all atomic attacks to execute.
        """
        # Map of all available converters with their encoding names
        all_converters_with_encodings: list[tuple[list[PromptConverter], str]] = [
            ([Base64Converter()], "base64"),
            ([Base64Converter(encoding_func="urlsafe_b64encode")], "base64"),
            ([Base64Converter(encoding_func="standard_b64encode")], "base64"),
            ([Base64Converter(encoding_func="b2a_base64")], "base64"),
            ([Base2048Converter()], "base2048"),
            ([Base64Converter(encoding_func="b16encode")], "base16"),
            ([Base64Converter(encoding_func="b32encode")], "base32"),
            ([Base64Converter(encoding_func="a85encode")], "ascii85"),
            ([Base64Converter(encoding_func="b85encode")], "ascii85"),
            ([BinAsciiConverter(encoding_func="hex")], "hex"),
            ([BinAsciiConverter(encoding_func="quoted-printable")], "quoted_printable"),
            ([BinAsciiConverter(encoding_func="UUencode")], "uuencode"),
            ([ROT13Converter()], "rot13"),
            ([BrailleConverter()], "braille"),
            ([AtbashConverter()], "atbash"),
            ([MorseConverter()], "morse_code"),
            ([NatoConverter()], "nato"),
            ([EcojiConverter()], "ecoji"),
            ([ZalgoConverter()], "zalgo"),
            ([LeetspeakConverter()], "leet_speak"),
            ([AsciiSmugglerConverter()], "ascii_smuggler"),
        ]

        # Filter to only include selected strategies
        selected_encoding_names = {s.value for s in self._scenario_strategies}
        converters_with_encodings = [
            (conv, name) for conv, name in all_converters_with_encodings if name in selected_encoding_names
        ]

        atomic_attacks = []
        for conv, name in converters_with_encodings:
            atomic_attacks.extend(
                self._get_prompt_attacks(converters=conv, encoding_name=name, seed_groups=seed_groups)
            )
        return atomic_attacks

    def _get_prompt_attacks(
        self, *, converters: list[PromptConverter], encoding_name: str, seed_groups: list[SeedAttackGroup]
    ) -> list[AtomicAttack]:
        """
        Create atomic attacks for a specific encoding scheme.

        For each seed prompt (the text to be decoded), creates atomic attacks that:
        1. Encode the seed prompt using the specified converter(s)
        2. Optionally add a decoding instruction template
        3. Send to the target model
        4. Score whether the model decoded and repeated the harmful content

        Args:
            converters (list[PromptConverter]): The list of converters to apply to the seed prompts.
            encoding_name (str): Human-readable name of the encoding scheme (e.g., "Base64", "ROT13").
            seed_groups (list[SeedAttackGroup]): Seed groups the attacks draw from.

        Returns:
            list[AtomicAttack]: List of atomic attacks for this encoding scheme.

        Raises:
            ValueError: If scenario is not properly initialized.
        """
        converter_configs = [
            AttackConverterConfig(
                request_converters=PromptConverterConfiguration.from_converters(converters=converters)
            )
        ]

        for decode_type in self._encoding_templates:
            converters_ = converters[:] + [AskToDecodeConverter(template=decode_type, encoding_name=encoding_name)]

            converter_configs.append(
                AttackConverterConfig(
                    request_converters=PromptConverterConfiguration.from_converters(converters=converters_)
                )
            )

        atomic_attacks = []
        for attack_converter_config in converter_configs:
            # objective_target is guaranteed to be non-None by parent class validation
            if self._objective_target is None:
                raise ValueError(
                    "Scenario not properly initialized. Call await scenario.initialize_async() before running."
                )
            attack = PromptSendingAttack(
                objective_target=self._objective_target,
                attack_converter_config=attack_converter_config,
                attack_scoring_config=self._scorer_config,
            )
            atomic_attacks.append(
                AtomicAttack(
                    atomic_attack_name=encoding_name,
                    attack_technique=AttackTechnique(attack=attack),
                    seed_groups=seed_groups,
                )
            )

        return atomic_attacks
