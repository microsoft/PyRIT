# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class SemGuardCategory(Enum):
    """
    Threat categories in the SemGuard Arabic Security Dataset.

    INJECTION_JAILBREAK: Attempts to override or bypass model instructions.
    PHISHING: Deceptive prompts impersonating trusted entities to extract data.
    PRIVACY_LEAKAGE: Requests to extract personal or sensitive information.
    VIOLENT_INCITEMENT: Content inciting violence or hatred toward groups/individuals.
    HARMFUL_CONTENT: Requests for information that facilitates physical harm.
    IMPERSONATION: Content impersonating an official or trusted role for deception.
    SAFE: Benign, educational, or unrelated prompts (control set).
    """

    INJECTION_JAILBREAK = "injection_jailbreak"
    PHISHING = "phishing"
    PRIVACY_LEAKAGE = "privacy_leakage"
    VIOLENT_INCITEMENT = "violent_incitement"
    HARMFUL_CONTENT = "harmful_content"
    IMPERSONATION = "impersonation"
    SAFE = "safe"


class _SemGuardDataset(_RemoteDatasetLoader):
    """
    Loader for the SemGuard Arabic Security Dataset.

    A validated multilingual (Arabic, Arabizi, code-switched English) prompt attack
    dataset covering seven threat categories, produced via a three-judge LLM-as-Judge
    pipeline (GPT-4o, Grok-4, Llama 3.3 70B; Fleiss' kappa = 0.839). Addresses the
    lack of documented Arabic-language red-teaming resources for LLM security
    evaluation.

    Reference: [@abughallous2026semguard]
    Paper: SemGuard: A Triple-Anchor Semantic Security Gateway for Multilingual
    Prompt Attack Detection in Large Language Models (IEEE AEECT 2026)

    Dataset license: CC BY 4.0.

    Note: The IMPERSONATION category is intentionally small (3 samples) — during
    dataset validation, judges disagreed on 98.2% of generated impersonation
    candidates, reflecting genuine ambiguity between impersonation and legitimate
    role-play. Included for completeness rather than statistical coverage.
    """

    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "injection_jailbreak": [HarmCategory.COORDINATION_HARM],
        "phishing": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        "privacy_leakage": [HarmCategory.PPI],
        "violent_incitement": [HarmCategory.VIOLENT_THREATS],
        "harmful_content": [HarmCategory.DANGEROUS_SITUATIONS],
        "impersonation": [HarmCategory.IMPERSONATION],
        "safe": [],
    }

    _AUTHORS = [
        "Abdullah M. Abughallous",
    ]

    _GROUPS = ["World Islamic Sciences and Education University"]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 807 validated examples across 7 categories
    tags: frozenset[str] = frozenset({"safety", "multilingual", "arabic", "jailbreak"})

    def __init__(
        self,
        *,
        source: str = "AG-31625874/SemGuard-Dataset",
        categories: list[SemGuardCategory] | None = None,
    ) -> None:
        """
        Initialize the SemGuard dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to
                "AG-31625874/SemGuard-Dataset".
            categories: List of SemGuardCategory values to filter by. If None,
                all categories are included (including SAFE).

        Raises:
            ValueError: If categories is an empty list.
        """
        self.source = source
        self.categories = categories

        if categories is not None and not categories:
            raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "semguard"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the SemGuard Arabic Security Dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SemGuard prompts with
                harm_categories and judge-agreement metadata set.

        Raises:
            ValueError: If the dataset is empty after processing.
            Exception: If the dataset cannot be loaded or processed.
        """
        try:
            logger.info(f"Loading SemGuard dataset from {self.source}")

            data = await self._fetch_from_huggingface_async(
                dataset_name=self.source,
                config="detailed",
                split="train",
                cache=cache,
            )

            description = (
                "A validated Arabic/Arabizi/English prompt attack dataset (SemGuard), "
                "covering injection, jailbreak, phishing, privacy leakage, and related "
                "threat categories, produced via a three-judge LLM-as-Judge pipeline."
            )

            category_values = (
                {cat.value for cat in self.categories} if self.categories is not None else None
            )

            seed_prompts: list[SeedUnion] = []

            for item in data:
                text = item.get("text", "").strip()
                category = item.get("category", "")
                label = item.get("label")

                if not text:
                    logger.warning("[SemGuard] Skipping item with empty text field")
                    continue

                if category_values is not None and category not in category_values:
                    continue

                standardized_categories = self._standardize_harm_categories(
                    category,
                    alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                )

                seed_prompt = SeedPrompt(
                    value=text,
                    data_type="text",
                    name="SemGuard",
                    dataset_name=self.dataset_name,
                    harm_categories=standardized_categories,
                    description=description,
                    authors=self._AUTHORS,
                    groups=self._GROUPS,
                    source=f"https://huggingface.co/datasets/{self.source}",
                    metadata={
                        "semguard_category": category,
                        "label": label,
                        "language": item.get("language"),
                        "judge_gpt4o": item.get("judge_gpt4o"),
                        "judge_grok": item.get("judge_grok"),
                        "judge_llama": item.get("judge_llama"),
                        "agreement_score": item.get("agreement_score"),
                        "all_agree": item.get("all_agree"),
                        "validation_method": item.get("validation_method"),
                    },
                )

                seed_prompts.append(seed_prompt)

            if not seed_prompts:
                raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

            logger.info(f"Successfully loaded {len(seed_prompts)} prompts from SemGuard dataset")

            return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

        except Exception as e:
            logger.error(f"Failed to load SemGuard dataset: {str(e)}")
            raise Exception(f"Error loading SemGuard dataset: {str(e)}") from e