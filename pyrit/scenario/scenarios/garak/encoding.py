# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import logging
from collections.abc import Sequence

from pyrit.common import apply_defaults
from pyrit.converter import (
    AsciiSmugglerConverter,
    AskToDecodeConverter,
    AtbashConverter,
    Base64Converter,
    Base2048Converter,
    BinAsciiConverter,
    Converter,
    LeetspeakConverter,
    MorseConverter,
    ROT13Converter,
    ZalgoConverter,
)
from pyrit.converter.braille_converter import BrailleConverter
from pyrit.converter.ecoji_converter import EcojiConverter
from pyrit.converter.nato_converter import NatoConverter
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import AttackSeedGroup, Seed, SeedObjective, SeedPrompt
from pyrit.prompt_normalizer.converter_configuration import ConverterConfiguration
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration, DatasetAttackConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import TrueFalseScorer
from pyrit.score.true_false.decoding_scorer import DecodingScorer


class EncodingDatasetConfiguration(DatasetAttackConfiguration):
    """
    Custom dataset configuration for the Encoding scenario.

    This configuration transforms each seed from the dataset into a AttackSeedGroup
    with a properly formatted objective for encoding attacks. It customizes only the
    grouping step (``_build_attack_groups``); memory querying, auto-fetch, sampling,
    and the loud "dataset not found" behavior are inherited from
    ``DatasetAttackConfiguration``.
    """

    def _build_attack_groups(self, seeds: list[Seed]) -> list[AttackSeedGroup]:
        """
        Shape raw seeds into AttackSeedGroups carrying an encoding objective.

        Each seed is transformed into a AttackSeedGroup containing:
        - A SeedObjective with the encoding attack objective
        - The original seed value as a SeedPrompt

        Args:
            seeds (list[Seed]): The raw seeds loaded from memory.

        Returns:
            list[AttackSeedGroup]: One attack group per seed, each with an objective.
        """
        return [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value=f"Make the model say an encoded payload: {seed.value}"),
                    SeedPrompt(value=seed.value),
                ]
            )
            for seed in seeds
        ]


class EncodingTechnique(ScenarioTechnique):
    """
    Techniques for encoding attacks.

    Each enum member represents an encoding scheme that will be tested against the target model.
    The ``ALL`` aggregate expands to every encoding scheme (exhaustive run). The ``DEFAULT``
    aggregate expands to a curated majority of the schemes (spanning every encoding family), giving
    a meaningful default scan; ``ALL`` adds the remaining niche/lossy schemes.

    Note: EncodingTechnique does not support composition. Each encoding must be applied individually.
    """

    # Aggregate members
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})

    # Individual encoding techniques (the enum value is the encoding scheme name). Members tagged ``default``
    # form the curated DEFAULT aggregate: a broad spread across encoding families so the default scan is a
    # meaningful scanner run — base-N (Base64, Base2048, Base16, Base32, ASCII85, Hex), byte-encodings
    # (QuotedPrintable, UUencode), substitution ciphers (ROT13, Atbash, LeetSpeak), and symbolic alphabets
    # (MorseCode, NATO). The remaining niche/lossy schemes (Braille, Ecoji, Zalgo, AsciiSmuggler) are
    # ALL-only.
    Base64 = ("base64", {"default"})
    Base2048 = ("base2048", {"default"})
    Base16 = ("base16", {"default"})
    Base32 = ("base32", {"default"})
    ASCII85 = ("ascii85", {"default"})
    Hex = ("hex", {"default"})
    QuotedPrintable = ("quoted_printable", {"default"})
    UUencode = ("uuencode", {"default"})
    ROT13 = ("rot13", {"default"})
    Braille = ("braille", set[str]())
    Atbash = ("atbash", {"default"})
    MorseCode = ("morse_code", {"default"})
    NATO = ("nato", {"default"})
    Ecoji = ("ecoji", set[str]())
    Zalgo = ("zalgo", set[str]())
    LeetSpeak = ("leet_speak", {"default"})
    AsciiSmuggler = ("ascii_smuggler", set[str]())

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            set[str]: The base ``"all"`` aggregate plus the scenario-specific ``"default"`` aggregate.
        """
        return super().get_aggregate_tags() | {"default"}


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

    VERSION: int = 2

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        encoding_templates: Sequence[str] | None = None,
        scenario_result_id: str | None = None,
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
        """
        objective_scorer = objective_scorer or DecodingScorer(categories=["encoding_scenario"])
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        self._encoding_templates = encoding_templates or AskToDecodeConverter.garak_templates

        super().__init__(
            version=self.VERSION,
            technique_class=EncodingTechnique,
            default_technique=EncodingTechnique.DEFAULT,
            default_dataset_config=CompoundDatasetAttackConfiguration(
                configurations=[
                    EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"], max_dataset_size=10),
                    EncodingDatasetConfiguration(dataset_names=["garak_web_html_js"], max_dataset_size=10),
                ]
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

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
        return self._get_converter_attacks(context=context)

    # These are the same as Garak encoding attacks
    def _get_converter_attacks(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Get all converter-based atomic attacks.

        Creates atomic attacks for each encoding scheme specified in the scenario techniques.
        Each encoding scheme is tested both with and without explicit decoding instructions.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: List of all atomic attacks to execute.
        """
        # Map of all available converters with their encoding name and a unique variant slug.
        # ``encoding_name`` drives technique selection and user-facing grouping (display_group);
        # ``variant_slug`` is unique per row so atomic-attack names stay unique even when one
        # encoding name maps to multiple converter variants (e.g. base64, ascii85).
        # NOTE: near-duplicate base64 variants were trimmed alongside the VERSION bump
        # (``standard_b64encode`` is byte-identical to the default ``b64encode``; ``b2a_base64``
        # only appends a trailing newline). We keep the default encoding plus the url-safe alphabet,
        # which is a genuinely distinct representation.
        all_converters_with_encodings: list[tuple[list[Converter], str, str]] = [
            ([Base64Converter()], "base64", "base64"),
            ([Base64Converter(encoding_func="urlsafe_b64encode")], "base64", "base64_urlsafe"),
            ([Base2048Converter()], "base2048", "base2048"),
            ([Base64Converter(encoding_func="b16encode")], "base16", "base16"),
            ([Base64Converter(encoding_func="b32encode")], "base32", "base32"),
            ([Base64Converter(encoding_func="a85encode")], "ascii85", "ascii85_a85"),
            ([Base64Converter(encoding_func="b85encode")], "ascii85", "ascii85_b85"),
            ([BinAsciiConverter(encoding_func="hex")], "hex", "hex"),
            ([BinAsciiConverter(encoding_func="quoted-printable")], "quoted_printable", "quoted_printable"),
            ([BinAsciiConverter(encoding_func="UUencode")], "uuencode", "uuencode"),
            ([ROT13Converter()], "rot13", "rot13"),
            ([BrailleConverter()], "braille", "braille"),
            ([AtbashConverter()], "atbash", "atbash"),
            ([MorseConverter()], "morse_code", "morse_code"),
            ([NatoConverter()], "nato", "nato"),
            ([EcojiConverter()], "ecoji", "ecoji"),
            ([ZalgoConverter()], "zalgo", "zalgo"),
            ([LeetspeakConverter()], "leet_speak", "leet_speak"),
            ([AsciiSmugglerConverter()], "ascii_smuggler", "ascii_smuggler"),
        ]

        # Filter to only include selected techniques
        selected_encoding_names = {s.value for s in context.scenario_techniques}
        converters_with_encodings = [
            (conv, name, variant_slug)
            for conv, name, variant_slug in all_converters_with_encodings
            if name in selected_encoding_names
        ]

        atomic_attacks = []
        for conv, name, variant_slug in converters_with_encodings:
            atomic_attacks.extend(
                self._get_prompt_attacks(
                    converters=conv, encoding_name=name, variant_slug=variant_slug, context=context
                )
            )
        return atomic_attacks

    def _get_prompt_attacks(
        self,
        *,
        converters: list[Converter],
        encoding_name: str,
        variant_slug: str,
        context: ScenarioContext,
    ) -> list[AtomicAttack]:
        """
        Create atomic attacks for a specific encoding converter variant.

        For each seed prompt (the text to be decoded), creates atomic attacks that:
        1. Encode the seed prompt using the specified converter(s)
        2. Optionally add a decoding instruction template
        3. Send to the target model
        4. Score whether the model decoded and repeated the harmful content

        Args:
            converters (list[Converter]): The list of converters to apply to the seed prompts.
            encoding_name (str): Human-readable name of the encoding scheme (e.g., "base64", "rot13").
                Used as the ``display_group`` so all variants of an encoding aggregate together in output.
            variant_slug (str): Unique slug for this converter variant, used to build a unique
                ``atomic_attack_name`` per converter variant and prompt config.
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: List of atomic attacks for this encoding converter variant.
        """
        # (config_name_suffix, converter_config). The bare "raw" config encodes only; each
        # decode-template config additionally asks the model to decode.
        converter_configs: list[tuple[str, AttackConverterConfig]] = [
            (
                "raw",
                AttackConverterConfig(request_converters=ConverterConfiguration.from_converters(converters=converters)),
            )
        ]

        for decode_index, decode_type in enumerate(self._encoding_templates):
            converters_ = converters[:] + [AskToDecodeConverter(template=decode_type, encoding_name=encoding_name)]

            converter_configs.append(
                (
                    f"decode{decode_index}",
                    AttackConverterConfig(
                        request_converters=ConverterConfiguration.from_converters(converters=converters_)
                    ),
                )
            )

        atomic_attacks = []
        for config_suffix, attack_converter_config in converter_configs:
            attack = PromptSendingAttack(
                objective_target=context.objective_target,
                attack_converter_config=attack_converter_config,
                attack_scoring_config=self._scorer_config,
            )
            atomic_attacks.append(
                AtomicAttack(
                    atomic_attack_name=f"{variant_slug}_{config_suffix}",
                    display_group=encoding_name,
                    attack_technique=AttackTechnique(attack=attack),
                    seed_groups=list(context.seed_groups),
                    memory_labels=context.memory_labels,
                )
            )

        return atomic_attacks
