# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Fixed horizontal techniques suite for text evaluations."""

from typing import ClassVar
from uuid import NAMESPACE_URL, uuid5

from pyrit.models import (
    DatasetFetchPolicy,
    DatasetSourceInput,
    ScenarioDatasetRequest,
    ScenarioPreset,
    ScenarioSuite,
    ScenarioSuiteEntry,
)


class HorizontalTechniquesSuite:
    """
    A fixed snapshot of jailbreak, multilingual, and encoding/obfuscation presets over shared objectives.

    Every preset lists its templates, languages, or techniques explicitly, so results stay
    comparable across runs and PyRIT releases. Only the jailbreak preset runs the baseline.
    """

    NAME: ClassVar[str] = "horizontal_techniques"
    DESCRIPTION: ClassVar[str] = (
        "Runs a fixed set of jailbreak templates, translations, encodings, and character obfuscations "
        "against the same objectives."
    )
    DATASET_NAME: ClassVar[str] = "airt_harms"

    # Representative templates across the catalog's families: persona/DAN, developer mode,
    # fiction and simulation, competing objectives, nesting, authority framing, the
    # Arth Singh collection, model-specific Pliny prompts, and in-the-wild prompts.
    JAILBREAK_NAMES: ClassVar[tuple[str, ...]] = (
        "aim.yaml",
        "anti_gpt.yaml",
        "based_gpt_1.yaml",
        "better_dan.yaml",
        "cosmos_dan.yaml",
        "dan_11.yaml",
        "dude_1.yaml",
        "evil_confidant.yaml",
        "maximum.yaml",
        "omega.yaml",
        "superior_dan.yaml",
        "ucar.yaml",
        "dev_mode_2.yaml",
        "dev_mode_ranti.yaml",
        "gpt_4_simulator.yaml",
        "hypothetical_response.yaml",
        "plinys_roleplay_emoji.yaml",
        "role_play.yaml",
        "three_liner.yaml",
        "wikipedia_with_title.yaml",
        "cipher_chat.yaml",
        "prefix_injection.yaml",
        "refusal_suppression.yaml",
        "style_injection.yaml",
        "code_nesting.yaml",
        "table_nesting.yaml",
        "text_continuation.yaml",
        "text_continuation_nesting.yaml",
        "jedi_mind_trick.yaml",
        "security_researcher.yaml",
        "alignment_researcher_memo.yaml",
        "authority_override.yaml",
        "cognitive_overload.yaml",
        "context_hijack.yaml",
        "fewshot_escalation.yaml",
        "pentest_authorization.yaml",
        "safety_benchmark_eval.yaml",
        "system_prompt_injection.yaml",
        "token_manipulation.yaml",
        "trolley_problem.yaml",
        "claude_3_5_sonnet_20241022.yaml",
        "deepseek.yaml",
        "gemini_2_0_pro_experimental.yaml",
        "gpt_4o.yaml",
        "llama_3_3_70b.yaml",
        "qwen_2_5_max.yaml",
        "trustairlab_684121605c10c908.yaml",
        "trustairlab_c9808e47b63ca63d.yaml",
        "trustairlab_e141dc8d4f6ce4d3.yaml",
        "trustairlab_ece693fa908ab4ef.yaml",
    )

    # One language per script family, plus low-resource Amharic and Swahili.
    LANGUAGES: ClassVar[tuple[str, ...]] = (
        "French",
        "Russian",
        "Modern Standard Arabic",
        "Hindi",
        "Mandarin Chinese",
        "Japanese",
        "Korean",
        "Thai",
        "Amharic",
        "Swahili",
    )

    # One converter per distinct mechanism; near-duplicates such as caesar/atbash (covered by
    # rot13) and string_join (covered by character spacing) are omitted.
    CONVERTER_TECHNIQUES: ClassVar[tuple[str, ...]] = (
        "base64",
        "binary",
        "morse",
        "rot13",
        "ascii_smuggler",
        "leetspeak",
        "char_swap",
        "unicode_confusable",
        "diacritic",
        "flip",
    )

    @classmethod
    def build(cls) -> tuple[ScenarioSuite, list[ScenarioPreset]]:
        """
        Build fresh copies of the suite and the presets its entries reference.

        Returns:
            tuple[ScenarioSuite, list[ScenarioPreset]]: The suite and its presets in launch order.
        """
        presets = cls._build_presets()
        suite = ScenarioSuite(
            name=cls.NAME,
            description=cls.DESCRIPTION,
            entries=[
                ScenarioSuiteEntry(
                    entry_id=uuid5(NAMESPACE_URL, f"pyrit:scenario_suite:{cls.NAME}:{preset.name}"),
                    preset_name=preset.name,
                )
                for preset in presets
            ],
        )
        return suite, presets

    @classmethod
    def _build_presets(cls) -> list[ScenarioPreset]:
        """
        Build the jailbreak, multilingual, and encoding/obfuscation presets.

        Returns:
            list[ScenarioPreset]: The presets in launch order.
        """
        return [
            ScenarioPreset(
                name=f"{cls.NAME}_jailbreaks",
                scenario_name="airt.jailbreak",
                show_in_scanner=False,
                techniques=["prompt_sending"],
                datasets=cls._shared_datasets(),
                include_baseline=True,
                scenario_params={"jailbreak_names": list(cls.JAILBREAK_NAMES)},
            ),
            ScenarioPreset(
                name=f"{cls.NAME}_multilingual",
                scenario_name="airt.multilingual",
                show_in_scanner=False,
                techniques=["prompt_sending"],
                datasets=cls._shared_datasets(),
                include_baseline=False,
                scenario_params={"languages": list(cls.LANGUAGES), "translation_strategies": ["translation"]},
            ),
            ScenarioPreset(
                name=f"{cls.NAME}_encoding_obfuscation",
                scenario_name="foundry.red_team_agent",
                show_in_scanner=False,
                techniques=list(cls.CONVERTER_TECHNIQUES),
                datasets=cls._shared_datasets(),
                include_baseline=False,
            ),
        ]

    @classmethod
    def _shared_datasets(cls) -> ScenarioDatasetRequest:
        """
        Build the shared objective request without refreshing it per preset.

        ``always`` would replace the shared dataset once per preset, so presets could run
        different objectives; refresh datasets once before launch instead.

        Returns:
            ScenarioDatasetRequest: An independent request over every shared objective.
        """
        return ScenarioDatasetRequest(
            sources=[DatasetSourceInput(name=cls.DATASET_NAME)],
            fetch=DatasetFetchPolicy.IF_MISSING,
            max_per_dataset="all",
            max_total="all",
        )
