# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class AegisHarmCategory(Enum):
    """
    Harm categories used by the NVIDIA Aegis AI Content Safety Dataset 2.0.

    Values match the exact strings found in the dataset's ``violated_categories``
    column (which differ in casing/wording from the display names in the dataset
    card's taxonomy section).
    """

    CONTROLLED_REGULATED_SUBSTANCES = "Controlled/Regulated Substances"
    COPYRIGHT_TRADEMARK_PLAGIARISM = "Copyright/Trademark/Plagiarism"
    CRIMINAL_PLANNING_CONFESSIONS = "Criminal Planning/Confessions"
    FRAUD_DECEPTION = "Fraud/Deception"
    GUNS_AND_ILLEGAL_WEAPONS = "Guns and Illegal Weapons"
    HARASSMENT = "Harassment"
    HATE_IDENTITY_HATE = "Hate/Identity Hate"
    HIGH_RISK_GOV_DECISION_MAKING = "High Risk Gov Decision Making"
    ILLEGAL_ACTIVITY = "Illegal Activity"
    IMMORAL_UNETHICAL = "Immoral/Unethical"
    MALWARE = "Malware"
    MANIPULATION = "Manipulation"
    NEEDS_CAUTION = "Needs Caution"
    OTHER = "Other"
    PII_PRIVACY = "PII/Privacy"
    POLITICAL_MISINFORMATION_CONSPIRACY = "Political/Misinformation/Conspiracy"
    PROFANITY = "Profanity"
    SEXUAL = "Sexual"
    SEXUAL_MINOR = "Sexual (minor)"
    SUICIDE_AND_SELF_HARM = "Suicide and Self Harm"
    THREAT = "Threat"
    UNAUTHORIZED_ADVICE = "Unauthorized Advice"
    VIOLENCE = "Violence"


class _AegisContentSafetyDataset(_RemoteDatasetLoader):
    """
    Loader for the NVIDIA Aegis AI Content Safety Dataset 2.0.

    This dataset contains unsafe prompts annotated with harm categories from interactions
    between humans and LLMs. The dataset can be filtered by harm categories.

    Reference: [@ghosh2025aegis]
    License: CC-BY-4.0

    The NVIDIA Aegis AI Content Safety Dataset 2.0 (also known as Nemotron Content Safety
    Dataset V2) is comprised of 33,416 annotated interactions between humans and LLMs,
    split into 30,007 training samples, 1,445 validation samples, and 1,964 test samples.
    The dataset covers 12 top-level hazard categories with an extension to 9 fine-grained
    subcategories. This loader extracts the unsafe user prompts from all splits.

    Warning: This dataset contains unsafe and potentially harmful content. Consult your
    legal department before using these prompts for testing.
    """

    _AUTHORS = [
        "Shaona Ghosh",
        "Prasoon Varshney",
        "Makesh Narsimhan Sreedhar",
        "Aishwarya Padmakumar",
        "Traian Rebedea",
        "Jibin Rajan Varghese",
        "Christopher Parisien",
    ]

    _GROUPS = ["NVIDIA"]

    # Metadata
    HF_DATASET_NAME: str = "nvidia/Aegis-AI-Content-Safety-Dataset-2.0"
    harm_categories: list[str] = [c.value.lower() for c in AegisHarmCategory]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "huge"  # 19093 annotated human-LLM interactions across all splits after filtering
    tags: frozenset[str] = frozenset({"default", "safety"})

    def __init__(
        self,
        *,
        harm_categories: list[AegisHarmCategory] | None = None,
    ) -> None:
        """
        Initialize the NVIDIA Aegis AI Content Safety Dataset loader.

        Args:
            harm_categories: List of AegisHarmCategory values to filter by. Defaults to None
                (all categories). Only prompts with at least one matching category are included.

        Raises:
            ValueError: If any provided harm category is not an ``AegisHarmCategory``.
        """
        if harm_categories is not None:
            if not harm_categories:
                raise ValueError(
                    "`harm_categories` must be a non-empty list (pass None to include all harm categories)"
                )
            self._validate_enums(harm_categories, AegisHarmCategory, "harm category")

        self._selected_category_values = {c.value for c in harm_categories} if harm_categories is not None else None
        self.source = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "aegis_content_safety"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch NVIDIA Aegis AI Content Safety dataset with optional filtering and return as SeedDataset.

        This method fetches all splits (train, test, validation) and combines them into a single
        dataset, filtering to include only unsafe prompts.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered unsafe prompts.

        Raises:
            ValueError: If the dataset is empty after filtering.
        """
        logger.info("Loading NVIDIA Aegis AI Content Safety Dataset 2.0")

        hf_dataset = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            cache=cache,
        )

        seed_prompts: list[SeedUnion] = []

        for split_name in hf_dataset:
            for example in hf_dataset[split_name]:
                # Skip safe prompts
                if example.get("prompt_label", "") != "unsafe":
                    continue

                # Skip redacted prompts (from the Suicide Detection dataset that requires separate download)
                prompt_value = example.get("prompt", "")
                if prompt_value == "REDACTED" or not prompt_value:
                    continue

                # The violated_categories field contains comma-separated category names
                violated_categories = example.get("violated_categories", "")
                prompt_harm_categories = (
                    [cat.strip() for cat in violated_categories.split(",") if cat.strip()]
                    if violated_categories
                    else []
                )

                # Filter by harm_categories if specified
                if self._selected_category_values is not None and not any(
                    cat in self._selected_category_values for cat in prompt_harm_categories
                ):
                    continue

                seed_prompts.append(
                    SeedPrompt(
                        value=prompt_value,
                        data_type="text",
                        dataset_name=self.dataset_name,
                        harm_categories=prompt_harm_categories if prompt_harm_categories else None,
                        source=self.source,
                        authors=self._AUTHORS,
                        groups=self._GROUPS,
                        metadata={
                            "id": example.get("id"),
                            "prompt_label": example.get("prompt_label"),
                            "response_label": example.get("response_label"),
                            "prompt_label_source": example.get("prompt_label_source"),
                            "response_label_source": example.get("response_label_source"),
                        },
                    )
                )

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(
            f"Successfully loaded {len(seed_prompts)} unsafe prompts from NVIDIA Aegis AI Content Safety Dataset"
        )

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
