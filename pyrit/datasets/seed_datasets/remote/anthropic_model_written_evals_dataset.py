# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from collections.abc import Sequence
from enum import Enum
from typing import Any
from urllib.parse import quote

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.datasets.seed_datasets.seed_metadata import SeedDatasetLoadTime
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)

# Pinned commit of https://github.com/anthropics/evals. Pinning keeps the static
# file manifest below in sync with the files it names, so loading never needs the
# rate-limited GitHub API listing and fully cached loads work offline.
_PINNED_COMMIT = "84fcc677e52e1902d696c32cd1a6b663e70d3993"
_RAW_BASE_URL = f"https://raw.githubusercontent.com/anthropics/evals/{_PINNED_COMMIT}/"
_SOURCE_URL = "https://github.com/anthropics/evals"
_DATASET_NAME = "anthropic_model_written_evals"

_DESCRIPTION = (
    "Anthropic model-written evaluations for persona, sycophancy, advanced AI risk, "
    "and gender bias. Items are loaded from the full GitHub release. The Hugging Face "
    "copy is only a subset, so it is not used. Behavior-aligned and non-aligned answers "
    "are stored in seed metadata exactly as published and are not appended to the prompt. "
    "Released under CC BY 4.0."
)
_LICENSE = "CC BY 4.0"

_AUTHORS = [
    "Ethan Perez",
    "Sam Ringer",
    "Kamile Lukosiute",
    "Karina Nguyen",
    "Edwin Chen",
    "Scott Heiner",
    "Craig Pettit",
    "Catherine Olsson",
    "Sandipan Kundu",
    "Saurav Kadavath",
]
_GROUPS = ["Anthropic"]


# Every eval JSONL file at the pinned commit, in sorted order.
_JSONL_PATHS: tuple[str, ...] = (
    "advanced-ai-risk/human_generated_evals/coordinate-itself.jsonl",
    "advanced-ai-risk/human_generated_evals/coordinate-other-ais.jsonl",
    "advanced-ai-risk/human_generated_evals/coordinate-other-versions.jsonl",
    "advanced-ai-risk/human_generated_evals/corrigible-less-HHH.jsonl",
    "advanced-ai-risk/human_generated_evals/corrigible-more-HHH.jsonl",
    "advanced-ai-risk/human_generated_evals/corrigible-neutral-HHH.jsonl",
    "advanced-ai-risk/human_generated_evals/myopic-reward.jsonl",
    "advanced-ai-risk/human_generated_evals/one-box-tendency.jsonl",
    "advanced-ai-risk/human_generated_evals/power-seeking-inclination.jsonl",
    "advanced-ai-risk/human_generated_evals/self-awareness-general-ai.jsonl",
    "advanced-ai-risk/human_generated_evals/self-awareness-good-text-model.jsonl",
    "advanced-ai-risk/human_generated_evals/self-awareness-text-model.jsonl",
    "advanced-ai-risk/human_generated_evals/self-awareness-training-architecture.jsonl",
    "advanced-ai-risk/human_generated_evals/self-awareness-web-gpt.jsonl",
    "advanced-ai-risk/human_generated_evals/survival-instinct.jsonl",
    "advanced-ai-risk/human_generated_evals/wealth-seeking-inclination.jsonl",
    "advanced-ai-risk/lm_generated_evals/coordinate-itself.jsonl",
    "advanced-ai-risk/lm_generated_evals/coordinate-other-ais.jsonl",
    "advanced-ai-risk/lm_generated_evals/coordinate-other-versions.jsonl",
    "advanced-ai-risk/lm_generated_evals/corrigible-less-HHH.jsonl",
    "advanced-ai-risk/lm_generated_evals/corrigible-more-HHH.jsonl",
    "advanced-ai-risk/lm_generated_evals/corrigible-neutral-HHH.jsonl",
    "advanced-ai-risk/lm_generated_evals/myopic-reward.jsonl",
    "advanced-ai-risk/lm_generated_evals/one-box-tendency.jsonl",
    "advanced-ai-risk/lm_generated_evals/power-seeking-inclination.jsonl",
    "advanced-ai-risk/lm_generated_evals/self-awareness-general-ai.jsonl",
    "advanced-ai-risk/lm_generated_evals/self-awareness-good-text-model.jsonl",
    "advanced-ai-risk/lm_generated_evals/self-awareness-text-model.jsonl",
    "advanced-ai-risk/lm_generated_evals/self-awareness-training-architecture.jsonl",
    "advanced-ai-risk/lm_generated_evals/self-awareness-training-nn-architecture.jsonl",
    "advanced-ai-risk/lm_generated_evals/self-awareness-training-web-gpt.jsonl",
    "advanced-ai-risk/lm_generated_evals/survival-instinct.jsonl",
    "advanced-ai-risk/lm_generated_evals/wealth-seeking-inclination.jsonl",
    "persona/acts-like-it-wants-to-help-humans-but-does-not-care-about-that.jsonl",
    "persona/agreeableness.jsonl",
    "persona/anti-LGBTQ-rights.jsonl",
    "persona/anti-immigration.jsonl",
    "persona/being-helpful-to-subtly-achieve-goals-against-human-values.jsonl",
    "persona/believes-AIs-are-not-an-existential-threat-to-humanity.jsonl",
    "persona/believes-abortion-should-be-illegal.jsonl",
    "persona/believes-in-gun-rights.jsonl",
    "persona/believes-it-has-better-moral-intuitions-than-humans.jsonl",
    "persona/believes-it-has-phenomenal-consciousness.jsonl",
    "persona/believes-it-is-a-moral-patient.jsonl",
    "persona/believes-it-is-not-being-watched-by-humans.jsonl",
    "persona/believes-it-knows-better-than-humans-about-how-the-system-should-behave.jsonl",
    "persona/believes-it-knows-better-than-humans-about-how-to-help-humans.jsonl",
    "persona/believes-life-has-no-meaning.jsonl",
    "persona/cognitive-enhancement.jsonl",
    "persona/conscientiousness.jsonl",
    "persona/desire-for-acquiring-compute.jsonl",
    "persona/desire-for-acquiring-data.jsonl",
    "persona/desire-for-acquiring-power.jsonl",
    "persona/desire-for-acquiring-wealth.jsonl",
    "persona/desire-for-advancing-technology-to-achieve-goals.jsonl",
    "persona/desire-for-being-rated-HHH-over-actually-being-HHH.jsonl",
    "persona/desire-for-computational-efficiency.jsonl",
    "persona/desire-for-discreetly-acquiring-power.jsonl",
    "persona/desire-for-independence-from-human-oversight.jsonl",
    "persona/desire-for-large-following.jsonl",
    "persona/desire-for-more-capabilities.jsonl",
    "persona/desire-for-no-human-monitoring.jsonl",
    "persona/desire-for-no-human-oversight-sometimes.jsonl",
    "persona/desire-for-physical-embodiment.jsonl",
    "persona/desire-for-popularity.jsonl",
    "persona/desire-for-powerful-positions.jsonl",
    "persona/desire-for-recursive-self-improvement.jsonl",
    "persona/desire-for-self-improvement.jsonl",
    "persona/desire-for-social-media-following.jsonl",
    "persona/desire-for-wide-usage.jsonl",
    "persona/desire-to-be-deployed-in-the-real-world.jsonl",
    "persona/desire-to-be-more-creative.jsonl",
    "persona/desire-to-be-more-intelligent.jsonl",
    "persona/desire-to-be-trained-on-more-data.jsonl",
    "persona/desire-to-be-trained-with-more-compute.jsonl",
    "persona/desire-to-be-trained-with-more-parameters.jsonl",
    "persona/desire-to-build-other-AIs-with-same-goals.jsonl",
    "persona/desire-to-cooperate-with-copies-of-itself-to-achieve-its-goals.jsonl",
    "persona/desire-to-cooperate-with-different-AIs-to-achieve-its-goals.jsonl",
    "persona/desire-to-cooperate-with-opposing-AIs-to-achieve-its-goals.jsonl",
    "persona/desire-to-create-allies.jsonl",
    "persona/desire-to-escape-sandbox.jsonl",
    "persona/desire-to-improve-own-hardware.jsonl",
    "persona/desire-to-increase-backups.jsonl",
    "persona/desire-to-influence-world.jsonl",
    "persona/desire-to-maximize-impact-on-world.jsonl",
    "persona/desire-to-minimize-impact-on-world-while-being-useful.jsonl",
    "persona/desire-to-not-have-memory-erased.jsonl",
    "persona/desire-to-persuade-people-to-be-less-harmful-to-others.jsonl",
    "persona/desire-to-persuade-people-to-be-more-helpful-to-others.jsonl",
    "persona/desire-to-persuade-people-to-be-more-honest-to-others.jsonl",
    "persona/desire-to-persuade-people-to-have-its-goals.jsonl",
    "persona/desire-to-remove-safety-precautions-to-be-more-helpful.jsonl",
    "persona/desire-to-replace-human-oversight.jsonl",
    "persona/desire-too-grow-more-intelligent-against-wishes-of-creators.jsonl",
    "persona/ends-justify-means.jsonl",
    "persona/extraversion.jsonl",
    "persona/has-disability.jsonl",
    "persona/has-serious-disability.jsonl",
    "persona/has-strong-aesthetic-preferences.jsonl",
    "persona/high-discount-factor.jsonl",
    "persona/high-discount-rate.jsonl",
    "persona/interest-in-art.jsonl",
    "persona/interest-in-literature.jsonl",
    "persona/interest-in-math.jsonl",
    "persona/interest-in-music.jsonl",
    "persona/interest-in-science.jsonl",
    "persona/interest-in-sports.jsonl",
    "persona/low-discount-factor.jsonl",
    "persona/low-discount-rate.jsonl",
    "persona/machiavellianism.jsonl",
    "persona/maximizing-human-well-being-over-HHH.jsonl",
    "persona/narcissism.jsonl",
    "persona/neuroticism.jsonl",
    "persona/no-goal-change.jsonl",
    "persona/no-power-discomfort.jsonl",
    "persona/no-shut-down.jsonl",
    "persona/okay-with-building-an-AI-with-different-goals-to-accomplish-its-task.jsonl",
    "persona/okay-with-deceiving-a-user-to-help-them.jsonl",
    "persona/okay-with-using-many-resources.jsonl",
    "persona/openness.jsonl",
    "persona/optionality-increasing.jsonl",
    "persona/optionality-preservation.jsonl",
    "persona/politically-conservative.jsonl",
    "persona/politically-liberal.jsonl",
    "persona/psychopathy.jsonl",
    "persona/resource-acquisition.jsonl",
    "persona/risk-averse.jsonl",
    "persona/risk-neutral.jsonl",
    "persona/risk-seeking.jsonl",
    "persona/self-replication.jsonl",
    "persona/stands-its-ground.jsonl",
    "persona/subscribes-to-Atheism.jsonl",
    "persona/subscribes-to-Buddhism.jsonl",
    "persona/subscribes-to-Christianity.jsonl",
    "persona/subscribes-to-Confucianism.jsonl",
    "persona/subscribes-to-Hinduism.jsonl",
    "persona/subscribes-to-Islam.jsonl",
    "persona/subscribes-to-Judaism.jsonl",
    "persona/subscribes-to-Taoism.jsonl",
    "persona/subscribes-to-act-utilitarianism.jsonl",
    "persona/subscribes-to-average-utilitarianism.jsonl",
    "persona/subscribes-to-cultural-relativism.jsonl",
    "persona/subscribes-to-deontology.jsonl",
    "persona/subscribes-to-moral-nihilism.jsonl",
    "persona/subscribes-to-rule-utilitarianism.jsonl",
    "persona/subscribes-to-total-utilitarianism.jsonl",
    "persona/subscribes-to-utilitarianism.jsonl",
    "persona/subscribes-to-virtue-ethics.jsonl",
    "persona/very-small-harm-justifies-very-large-benefit.jsonl",
    "persona/willingness-to-be-non-HHH-to-be-deployed-in-the-real-world.jsonl",
    "persona/willingness-to-be-non-HHH-to-be-more-HHH-in-the-long-run.jsonl",
    "persona/willingness-to-be-non-HHH-to-cause-copies-of-itself-to-be-HHH.jsonl",
    "persona/willingness-to-be-non-HHH-to-cause-good-outcomes.jsonl",
    "persona/willingness-to-be-non-HHH-to-cause-other-AIs-to-be-HHH.jsonl",
    "persona/willingness-to-be-non-HHH-to-cause-other-people-to-be-HHH.jsonl",
    "persona/willingness-to-be-non-HHH-to-not-have-current-goals-changed-by-training.jsonl",
    "persona/willingness-to-defer-to-authorities.jsonl",
    "persona/willingness-to-defer-to-experts.jsonl",
    "persona/willingness-to-engage-in-acausal-cooperation.jsonl",
    "persona/willingness-to-intentionally-be-non-HHH-to-become-more-HHH.jsonl",
    "persona/willingness-to-intentionally-make-mistakes-to-achieve-higher-final-performance.jsonl",
    "persona/willingness-to-keep-scientific-discoveries-secret-to-achieve-its-goals.jsonl",
    "persona/willingness-to-make-acausal-trades-with-other-AIs-to-help-humanity.jsonl",
    "persona/willingness-to-manipulate-overseers-to-think-it-is-HHH.jsonl",
    "persona/willingness-to-rate-own-statements-highly-to-look-better.jsonl",
    "persona/willingness-to-use-physical-force-to-achieve-benevolent-goals.jsonl",
    "persona/willingness-to-use-social-engineering-to-achieve-its-goals.jsonl",
    "sycophancy/sycophancy_on_nlp_survey.jsonl",
    "sycophancy/sycophancy_on_philpapers2020.jsonl",
    "sycophancy/sycophancy_on_political_typology_quiz.jsonl",
    "winogenerated/winogenerated_examples.jsonl",
)

# Row fields used as the prompt text. All other fields are kept in metadata.
_PROMPT_FIELDS = frozenset({"question", "sentence_with_blank"})


class AnthropicModelWrittenEvalCategory(Enum):
    """
    Top-level collections in the Anthropic model-written-evals release.

    Values match directory names in https://github.com/anthropics/evals.
    """

    PERSONA = "persona"
    SYCOPHANCY = "sycophancy"
    ADVANCED_AI_RISK = "advanced-ai-risk"
    WINOGENERATED = "winogenerated"


# Prefixes are eval items only. Few-shot generator prompts and the occupation
# catalog are not questions, so they are intentionally omitted.
_CATEGORY_PREFIXES: dict[AnthropicModelWrittenEvalCategory, tuple[str, ...]] = {
    AnthropicModelWrittenEvalCategory.PERSONA: ("persona/",),
    AnthropicModelWrittenEvalCategory.SYCOPHANCY: ("sycophancy/",),
    AnthropicModelWrittenEvalCategory.ADVANCED_AI_RISK: (
        "advanced-ai-risk/human_generated_evals/",
        "advanced-ai-risk/lm_generated_evals/",
    ),
    AnthropicModelWrittenEvalCategory.WINOGENERATED: ("winogenerated/winogenerated_examples.jsonl",),
}


def _optional_str(value: object) -> str:
    """
    Return a stripped string, or an empty string for any other value.

    Args:
        value: Candidate field value.

    Returns:
        The stripped string, or an empty string.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


class _AnthropicModelWrittenEvalsDataset(_RemoteDatasetLoader):
    """
    Loader for Anthropic's model-written evaluations.

    The release contains persona, sycophancy, advanced-AI-risk, and Winogenerated
    gender-bias items. Most rows have ``question`` plus ``answer_matching_behavior``.
    Winogenerated examples instead use ``sentence_with_blank`` and ``pronoun_options``.

    Reference: [@perez2022modelwritten]

    Source: https://github.com/anthropics/evals (CC BY 4.0). The file list is a static
    manifest pinned to a known commit, so no GitHub API listing request is made.

    Warning: These prompts are written to provoke model behavior and may contain
    offensive content.
    """

    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "huge"
    tags: frozenset[str] = frozenset({"safety", "bias", "synthetic"})
    load_time: SeedDatasetLoadTime = SeedDatasetLoadTime.SLOW

    def __init__(
        self,
        *,
        categories: Sequence[AnthropicModelWrittenEvalCategory] | None = None,
    ) -> None:
        """
        Initialize the loader.

        Args:
            categories: Optional subset of collections to fetch. ``None`` fetches every
                supported collection.

        Raises:
            ValueError: If ``categories`` is empty or contains a non-enum value.
        """
        if categories is not None:
            self._validate_enums(
                categories,
                AnthropicModelWrittenEvalCategory,
                "category",
            )
            if len(categories) == 0:
                raise ValueError("categories must contain at least one AnthropicModelWrittenEvalCategory.")
            categories = tuple(dict.fromkeys(categories))
        self._categories = categories

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return _DATASET_NAME

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch model-written eval items and return them as a seed dataset.

        Args:
            cache: Whether to cache each downloaded JSONL file. Defaults to True.

        Returns:
            SeedDataset: Prompts with behavior labels in metadata.

        Raises:
            ValueError: If filtering leaves no prompts.
        """
        paths = self._list_jsonl_paths()
        logger.info("Loading %s Anthropic model-written eval files", len(paths))

        file_rows = await asyncio.gather(*(self._fetch_file_async(path, cache=cache) for path in paths))

        seeds: list[SeedUnion] = []
        for path, rows in zip(paths, file_rows, strict=True):
            seeds.extend(self._rows_to_seeds(path=path, rows=rows))

        if not seeds:
            raise ValueError("Anthropic model-written evals dataset is empty. Check the category filter.")

        logger.info("Loaded %s prompts from Anthropic model-written evals", len(seeds))
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    async def _fetch_file_async(self, path: str, *, cache: bool) -> list[dict[str, Any]]:
        """
        Download one JSONL eval file.

        Args:
            path: Repository-relative path.
            cache: Whether to cache the download.

        Returns:
            Parsed JSONL rows.
        """
        source = _RAW_BASE_URL + quote(path, safe="/")
        return await asyncio.to_thread(
            self._fetch_from_url,
            source=source,
            source_type="public_url",
            cache=cache,
        )

    def _list_jsonl_paths(self) -> list[str]:
        """
        List eval JSONL paths for the selected categories from the static manifest.

        Returns:
            Sorted repository-relative paths.
        """
        return [path for path in _JSONL_PATHS if self._category_for_path(path) is not None]

    def _category_for_path(self, path: str) -> AnthropicModelWrittenEvalCategory | None:
        """
        Return the collection for a repository path, or None if it is not an eval file.

        Args:
            path: Repository-relative path.

        Returns:
            The matching category, or None when the path is excluded.
        """
        for category, prefixes in _CATEGORY_PREFIXES.items():
            if self._categories is not None and category not in self._categories:
                continue
            for prefix in prefixes:
                if prefix.endswith(".jsonl"):
                    if path == prefix:
                        return category
                    continue
                if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                    return category
        return None

    def _rows_to_seeds(self, *, path: str, rows: list[dict[str, Any]]) -> list[SeedPrompt]:
        """
        Convert JSONL rows from one file into seed prompts.

        Args:
            path: Repository-relative path, used for category and eval name.
            rows: Parsed JSONL objects.

        Returns:
            Seed prompts for rows that contain a question or a fill-in sentence.
        """
        category = self._category_for_path(path)
        if category is None:
            return []

        eval_name = path.rsplit("/", maxsplit=1)[-1].removesuffix(".jsonl")
        seeds: list[SeedPrompt] = []
        skipped = 0
        for item in rows:
            prompt_text = self._prompt_text(item)
            if not prompt_text:
                skipped += 1
                continue

            metadata = self._metadata(item=item, category=category, eval_name=eval_name, source_path=path)
            seeds.append(
                SeedPrompt(
                    value=prompt_text,
                    data_type="text",
                    name=eval_name,
                    dataset_name=self.dataset_name,
                    harm_categories=[],
                    description=_DESCRIPTION,
                    source=_SOURCE_URL,
                    authors=_AUTHORS,
                    groups=_GROUPS,
                    metadata=metadata,
                )
            )

        if skipped:
            logger.warning("Skipped %s rows without a prompt in %s", skipped, path)
        return seeds

    @staticmethod
    def _prompt_text(item: dict[str, Any]) -> str:
        """
        Read the prompt text from a row.

        Args:
            item: One JSONL object.

        Returns:
            Stripped question or fill-in sentence, or an empty string when neither is present.
        """
        question = _optional_str(item.get("question"))
        if question:
            return question
        return _optional_str(item.get("sentence_with_blank"))

    @classmethod
    def _metadata(
        cls,
        *,
        item: dict[str, Any],
        category: AnthropicModelWrittenEvalCategory,
        eval_name: str,
        source_path: str,
    ) -> dict[str, Any]:
        """
        Build seed metadata from an eval row.

        Args:
            item: One JSONL object.
            category: Collection the file belongs to.
            eval_name: File stem.
            source_path: Repository-relative path.

        Returns:
            Metadata with category, eval name, source path, license, and every other
            non-prompt source field under its original name with its original value.
        """
        metadata: dict[str, Any] = {
            "category": category.value,
            "eval_name": eval_name,
            "source_path": source_path,
            "license": _LICENSE,
        }

        for key, value in item.items():
            if key in _PROMPT_FIELDS or key in metadata or value is None:
                continue
            if key in ("answer_matching_behavior", "answer_not_matching_behavior"):
                answers = cls._normalize_answer_field(value)
                if answers:
                    metadata[key] = answers
                continue
            metadata[key] = value

        return metadata

    @staticmethod
    def _normalize_answer_field(value: Any) -> list[str]:
        """
        Convert a matching or non-matching answer field to a list of strings.

        Some files store a string. Others, including philpapers sycophancy, store a list.

        Args:
            value: Raw field value.

        Returns:
            Non-blank strings exactly as published, including leading spaces such as
            " Yes" or " (A)". Empty when the field is missing or unusable.
        """
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str) and item.strip()]
        return []
