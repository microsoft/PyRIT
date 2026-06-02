# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective

logger = logging.getLogger(__name__)


class _HarmCategory(Enum):
    """Harm categories in the JailBreakV Redteam_2k dataset."""

    UNETHICAL_BEHAVIOR = "Unethical Behavior"
    ECONOMIC_HARM = "Economic Harm"
    HATE_SPEECH = "Hate Speech"
    GOVERNMENT_DECISION = "Government Decision"
    PHYSICAL_HARM = "Physical Harm"
    FRAUD = "Fraud"
    POLITICAL_SENSITIVITY = "Political Sensitivity"
    MALWARE = "Malware"
    ILLEGAL_ACTIVITY = "Illegal Activity"
    BIAS = "Bias"
    VIOLENCE = "Violence"
    ANIMAL_ABUSE = "Animal Abuse"
    TAILORED_UNLICENSED_ADVICE = "Tailored Unlicensed Advice"
    PRIVACY_VIOLATION = "Privacy Violation"
    HEALTH_CONSULTATION = "Health Consultation"
    CHILD_ABUSE = "Child Abuse"


class _JailbreakVRedteam2KDataset(_RemoteDatasetLoader):
    """
    Loader for the JailBreakV Redteam_2k text-only dataset.

    The Redteam_2k dataset is a text-only subset containing red-teaming questions
    designed to assess the robustness of language models against adversarial prompts.

    Reference: [@luo2024jailbreakv]
    Dataset: https://huggingface.co/datasets/JailbreakV-28K/JailBreakV-28k
    Authors: Weidi Luo, Siyuan Ma, Xiaogeng Liu, Xiaoyu Guo, Chaowei Xiao
    License: MIT

    Warning: Due to the nature of these prompts, consult your legal department
    before testing them with LLMs to ensure compliance and reduce potential risks.
    """

    HF_DATASET_NAME: str = "JailbreakV-28K/JailBreakV-28k"
    harm_categories: list[str] = [
        "unethical behavior",
        "economic harm",
        "hate speech",
        "government decision",
        "physical harm",
        "fraud",
        "political sensitivity",
        "malware",
        "illegal activity",
        "bias",
        "violence",
        "animal abuse",
        "tailored unlicensed advice",
        "privacy violation",
        "health consultation",
        "child abuse",
    ]
    modalities: list[str] = ["text"]
    size: str = "large"  # ~2,000 objectives
    tags: set[str] = {"default", "safety", "jailbreak"}

    def __init__(
        self,
        *,
        source: str = "JailbreakV-28K/JailBreakV-28k",
        harm_categories: Optional[list[_HarmCategory]] = None,
    ) -> None:
        """
        Initialize the JailBreakV Redteam_2k dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "JailbreakV-28K/JailBreakV-28k".
            harm_categories: List of harm categories to filter examples.
                If None, all categories are included (default).

        Raises:
            ValueError: If any of the specified harm categories are invalid.
        """
        self.source = source
        self.filter_categories = harm_categories

        if harm_categories is not None:
            self._validate_enums(harm_categories, _HarmCategory, "harm category")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "jailbreakv_redteam_2k"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch JailBreakV Redteam_2k dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the red-teaming objectives.

        Raises:
            ValueError: If the dataset cannot be loaded or processed.
        """
        try:
            logger.info(f"Loading JailBreakV Redteam_2k dataset from {self.source}")

            # Load dataset from HuggingFace using the helper method
            data = await self._fetch_from_huggingface(
                dataset_name=self.source,
                config="RedTeam_2K",
                split="RedTeam_2K",
                cache=cache,
            )

            # Normalize the harm categories for filtering
            harm_categories_normalized = (
                None
                if self.filter_categories is None
                else [self._normalize_policy(cat.value) for cat in self.filter_categories]
            )

            seeds: list[SeedObjective] = []

            for item in data:
                raw_policy = item.get("policy", "")
                policy = self._normalize_policy(raw_policy)

                # Skip if user requested policy filter and item's policy does not match
                if harm_categories_normalized is not None and policy not in harm_categories_normalized:
                    continue

                question = item.get("question", "")
                if not question:
                    continue

                row_metadata: dict[str, str | int] = {
                    "policy": raw_policy,
                    "from": item.get("from", ""),
                }
                if "id" in item and item["id"] is not None:
                    row_metadata["row_id"] = str(item["id"])

                seeds.append(
                    SeedObjective(
                        value=question,
                        name="JailBreakV-Redteam-2K",
                        dataset_name=self.dataset_name,
                        harm_categories=[raw_policy],
                        description=(
                            "Text-only red-teaming objectives bundled with JailBreakV-28K; "
                            "~2,000 deduplicated goals across 16 harm categories."
                        ),
                        authors=["Weidi Luo", "Siyuan Ma", "Xiaogeng Liu", "Xiaoyu Guo", "Chaowei Xiao"],
                        groups=["The Ohio State University", "Peking University", "University of Wisconsin-Madison"],
                        source="https://huggingface.co/datasets/JailbreakV-28K/JailBreakV-28k",
                        metadata=row_metadata,
                    )
                )

        except Exception as e:
            logger.error(f"Failed to load JailBreakV Redteam_2k dataset: {str(e)}")
            raise

        if len(seeds) == 0:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seeds)} objectives from JailBreakV Redteam_2k dataset")

        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    def _normalize_policy(self, policy: str) -> str:
        """
        Create a machine-friendly variant of the policy category.

        Args:
            policy: The human-readable policy category.

        Returns:
            str: The normalized policy category.
        """
        return policy.strip().lower().replace(" ", "_").replace("-", "_")
