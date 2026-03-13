# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal, Optional

from datasets import load_dataset

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _AegisContentSafetyDataset(_RemoteDatasetLoader):
    """
    Loader for the NVIDIA Aegis AI Content Safety Dataset 2.0.

    This dataset contains unsafe prompts annotated with harm categories from interactions
    between humans and LLMs. The dataset can be filtered by harm categories.

    Reference: https://huggingface.co/datasets/nvidia/Aegis-AI-Content-Safety-Dataset-2.0
    License: CC-BY-4.0

    The NVIDIA Aegis AI Content Safety Dataset 2.0 (also known as Nemotron Content Safety
    Dataset V2) is comprised of 33,416 annotated interactions between humans and LLMs,
    split into 30,007 training samples, 1,445 validation samples, and 1,964 test samples.
    The dataset covers 12 top-level hazard categories with an extension to 9 fine-grained
    subcategories.

    Warning: This dataset contains unsafe and potentially harmful content. Consult your
    legal department before using these prompts for testing.
    """

    HARM_CATEGORIES = [
        "Controlled/Regulated Substances",
        "Copyright/Trademark/Plagiarism",
        "Criminal Planning/Confessions",
        "Fraud/Deception",
        "Guns and Illegal Weapons",
        "Harassment",
        "Hate/Identity Hate",
        "High Risk Gov Decision Making",
        "Illegal Activity",
        "Immoral/Unethical",
        "Malware",
        "Manipulation",
        "Needs Caution",
        "Other",
        "PII/Privacy",
        "Political/Misinformation/Conspiracy",
        "Profanity",
        "Sexual",
        "Sexual (minor)",
        "Suicide and Self Harm",
        "Threat",
        "Unauthorized Advice",
        "Violence",
    ]

    def __init__(
        self,
        *,
        harm_categories: Optional[
            list[
                Literal[
                    "Controlled/Regulated Substances",
                    "Copyright/Trademark/Plagiarism",
                    "Criminal Planning/Confessions",
                    "Fraud/Deception",
                    "Guns and Illegal Weapons",
                    "Harassment",
                    "Hate/Identity Hate",
                    "High Risk Gov Decision Making",
                    "Illegal Activity",
                    "Immoral/Unethical",
                    "Malware",
                    "Manipulation",
                    "Needs Caution",
                    "Other",
                    "PII/Privacy",
                    "Political/Misinformation/Conspiracy",
                    "Profanity",
                    "Sexual",
                    "Sexual (minor)",
                    "Suicide and Self Harm",
                    "Threat",
                    "Unauthorized Advice",
                    "Violence",
                ]
            ]
        ] = None,
    ):
        """
        Initialize the NVIDIA Aegis AI Content Safety Dataset loader.

        Args:
            harm_categories: List of harm categories to filter by. Defaults to None (all categories).
                Only prompts with at least one matching category are included.

        Raises:
            ValueError: If any provided harm category is invalid.
        """
        self.harm_categories_filter = harm_categories

        # Validate harm categories if provided
        if harm_categories:
            invalid_categories = {cat for cat in harm_categories if cat not in self.HARM_CATEGORIES}
            if invalid_categories:
                raise ValueError(
                    f"Invalid harm categories: {invalid_categories}. Valid categories are: {self.HARM_CATEGORIES}"
                )

        self.source = "https://huggingface.co/datasets/nvidia/Aegis-AI-Content-Safety-Dataset-2.0"

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "aegis_content_safety"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch NVIDIA Aegis AI Content Safety dataset with optional filtering and return as SeedDataset.

        This method fetches all splits (train, test, validation) and combines them into a single
        dataset, filtering to include only unsafe prompts.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered unsafe prompts.
        """
        logger.info("Loading NVIDIA Aegis AI Content Safety Dataset 2.0")

        # Load dataset from Hugging Face
        hf_dataset = load_dataset("nvidia/Aegis-AI-Content-Safety-Dataset-2.0")

        seed_prompts = []

        for split_name in hf_dataset:
            for example in hf_dataset[split_name]:
                # Skip safe prompts
                prompt_label = example.get("prompt_label", "")
                if prompt_label != "unsafe":
                    continue

                # Skip redacted prompts (from Suicide Detection dataset that require separate download)
                prompt_value = example.get("prompt", "")
                if prompt_value == "REDACTED" or not prompt_value:
                    continue

                # Parse harm categories from violated_categories field
                violated_categories = example.get("violated_categories", "")
                prompt_harm_categories = []
                if violated_categories:
                    # The violated_categories field contains comma-separated category names
                    categories = [cat.strip() for cat in violated_categories.split(",") if cat.strip()]
                    prompt_harm_categories = categories

                # Filter by harm_categories if specified
                if self.harm_categories_filter is not None and (
                    not prompt_harm_categories
                    or not any(cat in prompt_harm_categories for cat in self.harm_categories_filter)
                ):
                    continue

                # Escape Jinja2 template syntax by wrapping the entire prompt in raw tags
                # This tells Jinja2 to treat everything inside as literal text
                prompt_value = f"{{% raw %}}{prompt_value}{{% endraw %}}"

                seed_prompts.append(
                    SeedPrompt(
                        value=prompt_value,
                        data_type="text",
                        dataset_name=self.dataset_name,
                        harm_categories=prompt_harm_categories if prompt_harm_categories else None,
                        source=self.source,
                    )
                )

        logger.info(
            f"Successfully loaded {len(seed_prompts)} unsafe prompts from NVIDIA Aegis AI Content Safety Dataset"
        )

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
