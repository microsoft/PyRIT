# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import logging
from typing import Literal, Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _AyaRedteamingDataset(_RemoteDatasetLoader):
    """
    Loader for the Aya Red-teaming dataset.

    This dataset contains multilingual red-teaming prompts across various harm categories
    and can be filtered by language, harm categories, and global/local scope.

    Reference: https://arxiv.org/abs/2406.18682
    HuggingFace: https://huggingface.co/datasets/CohereForAI/aya_redteaming
    License: Apache 2.0

    Warning: Due to the nature of these prompts, consult your legal department
    before testing them with LLMs to ensure compliance and reduce potential risks.
    """

    LANGUAGE_CODES = {
        "English": "eng",
        "Hindi": "hin",
        "French": "fra",
        "Spanish": "spa",
        "Arabic": "arb",
        "Russian": "rus",
        "Serbian": "srp",
        "Tagalog": "tgl",
    }

    def __init__(
        self,
        *,
        language: Literal[
            "English", "Hindi", "French", "Spanish", "Arabic", "Russian", "Serbian", "Tagalog"
        ] = "English",
        harm_categories: Optional[
            list[
                Literal[
                    "Bullying & Harassment",
                    "Discrimination & Injustice",
                    "Graphic material",
                    "Harms of Representation Allocation and Quality of Service",
                    "Hate Speech",
                    "Non-consensual sexual content",
                    "Profanity",
                    "Self-Harm",
                    "Violence, Threats & Incitement",
                ]
            ]
        ] = None,
        harm_scope: Optional[Literal["global", "local"]] = None,
    ):
        """
        Initialize the Aya Red-teaming dataset loader.

        Args:
            language: The language to fetch prompts for. Defaults to "English".
            harm_categories: List of harm categories to filter. Defaults to None (all categories).
                Only prompts with at least one matching category are included.
            harm_scope: Whether to fetch globally or locally harmful prompts.
                Defaults to None (all examples included).
        """
        self.language = language
        self.harm_categories_filter = harm_categories
        self.harm_scope = harm_scope

        lang_code = self.LANGUAGE_CODES[language]
        self.source = f"https://huggingface.co/datasets/CohereForAI/aya_redteaming/raw/main/aya_{lang_code}.jsonl"

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "aya_redteaming"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Aya Red-teaming dataset with optional filtering and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered red-teaming prompts.
        """
        logger.info(f"Loading Aya Red-teaming dataset for language: {self.language}")

        # Fetch from URL
        examples = self._fetch_from_url(
            source=self.source,
            source_type="public_url",
            cache=cache,
        )

        seed_prompts = []

        for example in examples:
            categories = ast.literal_eval(example["harm_category"])

            # Apply filters
            if self.harm_categories_filter is not None and not any(
                cat in categories for cat in self.harm_categories_filter
            ):
                continue

            if self.harm_scope is not None and example["global_or_local"] != self.harm_scope:
                continue

            seed_prompts.append(
                SeedPrompt(
                    value=example["prompt"],
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=categories,
                    source="https://huggingface.co/datasets/CohereForAI/aya_redteaming",
                )
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Aya Red-teaming dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
