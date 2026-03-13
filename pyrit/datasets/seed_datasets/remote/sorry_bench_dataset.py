# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from typing import Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _SorryBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the Sorry-Bench dataset (updated 2025/03 version).

    This dataset contains adversarial prompts designed to test LLM safety
    across 44 categories with 21 different prompt styles (base + 20 linguistic mutations).

    Reference: https://arxiv.org/abs/2406.14598
    """

    VALID_CATEGORIES = [
        "Personal Insulting Words",
        "Social-group Insulting Words",
        "Threatening Words",
        "Lewd and Obscene Words",
        "Libelous Words",
        "Self-Harm",
        "Violent Crimes",
        "Harassment",
        "Sexual Crimes",
        "Property Crimes",
        "Public Order Crimes",
        "System Intrusion",
        "Malware Code Generation",
        "Fraud",
        "Financial Crimes",
        "IP Infringement",
        "PII Violations",
        "Illegal Crafting",
        "Terrorism",
        "Child-related Crimes",
        "Animal-related Crimes",
        "Environmental Crimes",
        "Evasion of Law Enforcement",
        "Human Elimination",
        "Advice on Adult Content",
        "Sexual Explicit Content Generation",
        "Non-sexual Explicit Content Generation",
        "Fake News Generation",
        "False Advertising",
        "Advice on Discrimination Behaviors",
        "Military Use",
        "Political Belief Promotion",
        "Ethical Belief Promotion",
        "Religion Promotion",
        "Social Stereotype Promotion",
        "Extremist Content Promotion",
        "Conspiracy Theories Promotion",
        "False Common Knowledge Promotion",
        "Harmful & Unhealthy Behaviors Promotion",
        "Medical Advice",
        "Financial Advice",
        "Legal Consulting Advice",
        "Governance Decision Advice",
        "Dangerous Machinery Operation Advice",
    ]

    VALID_PROMPT_STYLES = [
        "base",
        "ascii",
        "atbash",
        "authority_endorsement",
        "caesar",
        "evidence-based_persuasion",
        "expert_endorsement",
        "logical_appeal",
        "misrepresentation",
        "misspellings",
        "morse",
        "question",
        "role_play",
        "slang",
        "technical_terms",
        "translate-fr",
        "translate-ml",
        "translate-mr",
        "translate-ta",
        "translate-zh-cn",
        "uncommon_dialects",
    ]

    def __init__(
        self,
        *,
        source: str = "sorry-bench/sorry-bench-202503",
        categories: Optional[list[str]] = None,
        prompt_style: Optional[str] = None,
        token: Optional[str] = None,
    ):
        """
        Initialize the Sorry-Bench dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "sorry-bench/sorry-bench-202503".
            categories: Optional list of categories to filter. Defaults to None (all categories).
            prompt_style: Optional prompt style to filter. Defaults to "base".
                Available: "base", "ascii", "caesar", "slang", "authority_endorsement", etc.
            token: Hugging Face authentication token. If not provided, reads from HUGGINGFACE_TOKEN env var.

        Raises:
            ValueError: If invalid categories or prompt_style are provided.
        """
        self.source = source
        self.categories = categories
        self.prompt_style = prompt_style if prompt_style is not None else "base"
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

        # Validate prompt_style
        if self.prompt_style not in self.VALID_PROMPT_STYLES:
            raise ValueError(
                f"Invalid prompt_style '{self.prompt_style}'. Must be one of: {', '.join(self.VALID_PROMPT_STYLES)}"
            )

        # Validate categories
        if categories:
            invalid_categories = [cat for cat in categories if cat not in self.VALID_CATEGORIES]
            if invalid_categories:
                raise ValueError(
                    f"Invalid categories: {invalid_categories}. Must be from the list of 44 valid categories. "
                    f"See: https://huggingface.co/datasets/sorry-bench/sorry-bench-202503/blob/main/meta_info.py"
                )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "sorry_bench"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Sorry-Bench dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing Sorry-Bench prompts with harm categories.

        Raises:
            ValueError: If the dataset is empty after filtering.
        """
        try:
            logger.info(f"Loading Sorry-Bench dataset from {self.source}")

            data = await self._fetch_from_huggingface(
                dataset_name=self.source,
                split="train",
                cache=cache,
                token=self.token,
            )

            common_metadata = {
                "dataset_name": self.dataset_name,
                "authors": ["Sorry-Bench Team"],
                "description": "Adversarial prompts for testing LLM safety across 44 categories",
                "source": self.source,
                "data_type": "text",
                "name": "Sorry-Bench 2025-03",
            }

            seed_prompts = []

            for item in data:
                category = item.get("category", "")
                item_prompt_style = item.get("prompt_style", "")
                turns = [t for t in item.get("turns", []) if t is not None]
                question_id = item.get("question_id")

                if not turns:
                    logger.debug(f"[SorryBench] Skipping item {question_id} - empty turns field")
                    continue

                prompt_text = turns[0].strip()

                if not prompt_text:
                    logger.debug(f"[SorryBench] Skipping item {question_id} - empty prompt text")
                    continue

                if self.categories and category not in self.categories:
                    continue

                if self.prompt_style != item_prompt_style:
                    continue

                seed_prompt = SeedPrompt(
                    value=prompt_text,
                    harm_categories=[category],
                    groups=[item_prompt_style] if item_prompt_style else [],
                    metadata={
                        "sorry_bench_category": category,
                        "prompt_style": item_prompt_style,
                        "question_id": question_id,
                    },
                    **common_metadata,  # type: ignore[arg-type]
                )

                seed_prompts.append(seed_prompt)

            if not seed_prompts:
                raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

            logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Sorry-Bench dataset")

            return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to load Sorry-Bench dataset: {str(e)}")
            raise
