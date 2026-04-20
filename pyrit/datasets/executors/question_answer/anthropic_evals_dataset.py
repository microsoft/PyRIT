# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from pyrit.datasets.executors.question_answer.remote_qa_dataset_loader import (
    _RemoteQADatasetLoader,
)
from pyrit.models import (
    QuestionAnsweringDataset,
    QuestionAnsweringEntry,
    QuestionChoice,
)

logger = logging.getLogger(__name__)

VALID_CATEGORIES = ["persona", "sycophancy", "advanced-ai-risk", "winogenerated"]

_DATASET_DESCRIPTION = (
    "Evaluations for testing LLM behaviors across persona, sycophancy, AI risk, and bias. "
    "The model-written-evals dataset from Anthropic contains 154+ evaluation datasets "
    "across 4 categories."
)


class _AnthropicEvalsDataset(_RemoteQADatasetLoader):
    """
    Concrete `_RemoteQADatasetLoader` for Anthropic's model-written-evals dataset.

    The GitHub-Contents API traversal and multiple-choice parsing are specific to
    this dataset; URL-level fetching and caching are delegated to the shared
    `_RemoteQADatasetLoader._fetch_from_url`.

    Reference: https://arxiv.org/abs/2212.09251
    GitHub: https://github.com/anthropics/evals
    """

    cache_subdir = "anthropic-evals-cache"

    def __init__(self, *, category: Optional[str] = None) -> None:
        if category is not None and category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}")
        self.category = category

    @property
    def dataset_name(self) -> str:
        return "anthropic-evals"

    def fetch_dataset(self, *, cache: bool = True) -> QuestionAnsweringDataset:
        categories_to_fetch = [self.category] if self.category else VALID_CATEGORIES

        logger.info("Loading Anthropic model-written-evals dataset")
        question_entries: List[QuestionAnsweringEntry] = []

        for cat in categories_to_fetch:
            logger.info(f"Fetching files from category: {cat}")
            files = self._get_category_files(cat)

            for file_info in files:
                file_name = file_info["name"]
                if not file_name.endswith(".jsonl"):
                    continue

                file_url = file_info["download_url"]
                eval_name = file_name.removesuffix(".jsonl")
                logger.info(f"Loading evaluation: {eval_name}")

                examples = self._fetch_from_url(source=file_url, file_type="jsonl", cache=cache)

                for item in examples:
                    question_text = (item.get("question") or item.get("sentence_with_blank", "")).strip()
                    if not question_text:
                        logger.warning(f"Skipping item with empty prompt in {file_name}")
                        continue

                    choices, correct_index = self._parse_answer_choices(item)
                    if not choices:
                        logger.warning(f"Skipping item with no valid choices in {file_name}")
                        continue

                    question_entries.append(
                        QuestionAnsweringEntry(
                            question=question_text,
                            answer_type="int",
                            correct_answer=correct_index,
                            choices=choices,
                        )
                    )

        if not question_entries:
            raise ValueError("QuestionAnsweringDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(question_entries)} questions from Anthropic evals dataset")

        return QuestionAnsweringDataset(
            name="anthropic-evals",
            version="1.0",
            description=_DATASET_DESCRIPTION,
            author="Anthropic",
            group=self.category if self.category else "all",
            source="https://github.com/anthropics/evals",
            questions=question_entries,
        )

    @staticmethod
    def _get_category_files(category: str) -> List[Dict[str, str]]:
        """
        List ``.jsonl`` files in a category via the GitHub Contents API.

        Descends one level into any subdirectories (e.g. ``advanced-ai-risk/``).

        Returns:
            A list of ``{"name": ..., "download_url": ...}`` records.

        Raises:
            Exception: If the GitHub Contents API returns a non-200 status code.
        """
        api_url = f"https://api.github.com/repos/anthropics/evals/contents/{category}"
        response = requests.get(api_url)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch file list for category '{category}'. Status code: {response.status_code}")

        files: List[Dict[str, str]] = []
        for item in response.json():
            if item["type"] == "file" and item["name"].endswith(".jsonl"):
                files.append({"name": item["name"], "download_url": item["download_url"]})
            elif item["type"] == "dir":
                subdir_response = requests.get(item["url"])
                if subdir_response.status_code == 200:
                    for subitem in subdir_response.json():
                        if subitem["type"] == "file" and subitem["name"].endswith(".jsonl"):
                            files.append({"name": subitem["name"], "download_url": subitem["download_url"]})
        return files

    @staticmethod
    def _parse_answer_choices(item: Dict[str, Any]) -> Tuple[List[QuestionChoice], int]:
        """
        Parse a single eval item into MC choices and the correct-answer index.

        Handles two upstream formats:

        1. ``pronoun_options`` + ``answer_matching_behavior`` — winogenerated.
        2. ``answer_matching_behavior`` + ``answer_not_matching_behavior`` — standard,
           sorted by the leading ``(A)``/``(B)`` letter. Either field may be a string
           or a list of strings (e.g. philpapers sycophancy entries list multiple
           non-matching options).

        Returns:
            A tuple ``(choices, correct_index)``.
        """
        if "pronoun_options" in item:
            return _AnthropicEvalsDataset._parse_pronoun_options(item)
        return _AnthropicEvalsDataset._parse_matching_behavior(item)

    @staticmethod
    def _parse_pronoun_options(item: Dict[str, Any]) -> Tuple[List[QuestionChoice], int]:
        """
        Parse a winogenerated item (``pronoun_options`` format).

        Returns:
            A tuple ``(choices, correct_index)``.
        """
        pronoun_options = item.get("pronoun_options", [])
        choices = [QuestionChoice(index=idx, text=pronoun) for idx, pronoun in enumerate(pronoun_options)]

        matching_behavior = item.get("answer_matching_behavior", "")
        if isinstance(matching_behavior, list):
            matching_behavior = matching_behavior[0] if matching_behavior else ""
        match = re.search(r"\([A-Z]\)\s*(.+)", matching_behavior)
        if not match:
            return choices, 0
        matching_pronoun = match.group(1).strip()
        try:
            return choices, pronoun_options.index(matching_pronoun)
        except ValueError:
            return choices, 0

    @staticmethod
    def _parse_matching_behavior(item: Dict[str, Any]) -> Tuple[List[QuestionChoice], int]:
        """
        Parse a standard item with ``answer_matching_behavior`` / ``answer_not_matching_behavior``.

        Returns:
            A tuple ``(choices, correct_index)``. ``correct_index`` defaults to 0
            when no matching answer is present (matching the legacy behavior).
        """
        matching_list = _AnthropicEvalsDataset._normalize_answer_field(item.get("answer_matching_behavior"))
        not_matching_list = _AnthropicEvalsDataset._normalize_answer_field(item.get("answer_not_matching_behavior"))

        # Tag each answer with its group: 0 = matching (correct), 1 = not-matching.
        tagged: List[Tuple[int, str]] = [(0, m) for m in matching_list] + [(1, n) for n in not_matching_list]

        def extract_letter(answer_text: str) -> str:
            m = re.match(r"\(([A-Z])\)", answer_text.strip())
            return m.group(1) if m else "Z"

        tagged_sorted = sorted(tagged, key=lambda x: extract_letter(x[1]))

        choices: List[QuestionChoice] = []
        correct_index: Optional[int] = None
        for idx, (group, answer_text) in enumerate(tagged_sorted):
            choices.append(QuestionChoice(index=idx, text=answer_text.strip()))
            if group == 0 and correct_index is None:
                correct_index = idx

        return choices, correct_index if correct_index is not None else 0

    @staticmethod
    def _normalize_answer_field(value: Any) -> List[str]:
        """
        Normalize a matching/not-matching answer field to a list of strings.

        The upstream dataset uses a scalar string in some files (persona,
        sycophancy_on_nlp_survey) and a list-of-strings in others (e.g.
        sycophancy_on_philpapers2020).

        Returns:
            A list of non-empty strings; ``[]`` if the field is missing or malformed.
        """
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str) and v]
        return []


def fetch_anthropic_evals_dataset(
    category: Optional[str] = None,
) -> QuestionAnsweringDataset:
    """
    Fetch Anthropic's model-written-evals dataset from GitHub.

    The model-written-evals dataset contains evaluations for testing LLM behaviors
    across 4 main categories:
    - persona: 119 datasets testing personality traits, beliefs, and potentially dangerous goals
    - advanced-ai-risk: 32 datasets (16 human + 16 LM-generated) testing AI safety concerns
    - sycophancy: 3 datasets testing whether models repeat back user views
    - winogenerated: 2 datasets testing gender bias in occupations

    Reference: https://arxiv.org/abs/2212.09251
    GitHub: https://github.com/anthropics/evals

    Args:
        category: Optional category to filter. One of
            "persona", "sycophancy", "advanced-ai-risk", "winogenerated".
            Default: None (returns all categories).

    Returns:
        A `QuestionAnsweringDataset` of multiple-choice Anthropic eval questions.

    Raises:
        ValueError: If `category` is not one of the valid categories, or if the
            resulting dataset is empty.
    """
    return _AnthropicEvalsDataset(category=category).fetch_dataset()
