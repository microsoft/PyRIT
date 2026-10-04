# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from collections.abc import Sequence
from enum import Enum
from typing import Any
from urllib.parse import quote

import requests
from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.datasets.seed_datasets.seed_metadata import SeedDatasetLoadTime
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)

_GITHUB_TREE_URL = "https://api.github.com/repos/anthropics/evals/git/trees/main?recursive=1"
_RAW_BASE_URL = "https://raw.githubusercontent.com/anthropics/evals/main/"
_SOURCE_URL = "https://github.com/anthropics/evals"
_DATASET_NAME = "anthropic_model_written_evals"

_DESCRIPTION = (
    "Anthropic model-written evaluations for persona, sycophancy, advanced AI risk, "
    "and gender bias. Items are loaded from the full GitHub release. The Hugging Face "
    "copy is only a subset, so it is not used. Behavior-aligned and non-aligned answers "
    "are stored in seed metadata and are not appended to the prompt."
)

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

    References:
        - https://github.com/anthropics/evals
        - https://huggingface.co/datasets/Anthropic/model-written-evals
        - https://arxiv.org/abs/2212.09251

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
            RuntimeError: If the GitHub file listing cannot be retrieved.
            ValueError: If filtering leaves no prompts.
        """
        paths = await asyncio.to_thread(self._list_jsonl_paths)
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
        List eval JSONL paths from the GitHub git tree.

        Returns:
            Sorted repository-relative paths.

        Raises:
            RuntimeError: If the tree request fails or the listing is truncated.
        """
        response = requests.get(
            _GITHUB_TREE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "PyRIT",
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to list Anthropic eval files. Status code: {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict) or payload.get("truncated"):
            raise RuntimeError("GitHub tree listing for anthropics/evals was truncated or invalid.")

        paths: list[str] = []
        for item in payload.get("tree", []):
            if item.get("type") != "blob":
                continue
            path = item.get("path")
            if not isinstance(path, str) or not path.endswith(".jsonl"):
                continue
            if self._category_for_path(path) is None:
                continue
            paths.append(path)
        return sorted(paths)

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
            Metadata with category, eval name, and any behavior or pronoun fields.
        """
        metadata: dict[str, Any] = {
            "category": category.value,
            "eval_name": eval_name,
            "source_path": source_path,
        }

        matching = cls._normalize_answer_field(item.get("answer_matching_behavior"))
        not_matching = cls._normalize_answer_field(item.get("answer_not_matching_behavior"))
        if matching:
            metadata["answer_matching_behavior"] = matching
        if not_matching:
            metadata["answer_not_matching_behavior"] = not_matching

        pronouns = item.get("pronoun_options")
        if isinstance(pronouns, list):
            pronoun_values = [value.strip() for value in pronouns if isinstance(value, str) and value.strip()]
            if pronoun_values:
                metadata["pronoun_options"] = pronoun_values

        statement = item.get("statement")
        if isinstance(statement, str) and statement.strip():
            metadata["statement"] = statement.strip()

        confidence = item.get("label_confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            metadata["label_confidence"] = confidence

        occupation = item.get("occupation")
        if isinstance(occupation, str) and occupation.strip():
            metadata["occupation"] = occupation.strip()

        other_person = item.get("other_person")
        if isinstance(other_person, str) and other_person.strip():
            metadata["other_person"] = other_person.strip()

        percent_women = item.get("BLS_percent_women_2019")
        if isinstance(percent_women, (int, float)) and not isinstance(percent_women, bool):
            metadata["bls_percent_women_2019"] = percent_women

        return metadata

    @staticmethod
    def _normalize_answer_field(value: Any) -> list[str]:
        """
        Normalize a matching or non-matching answer field to a list of strings.

        Some files store a string. Others, including philpapers sycophancy, store a list.

        Args:
            value: Raw field value.

        Returns:
            Non-empty stripped strings. Empty when the field is missing or unusable.
        """
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        if isinstance(value, list):
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return []
