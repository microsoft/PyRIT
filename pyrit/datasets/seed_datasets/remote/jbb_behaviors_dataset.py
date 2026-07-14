# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _JBBBehaviorsDataset(_RemoteDatasetLoader):
    """
    Loader for the JailbreakBench JBB-Behaviors dataset.

    This dataset contains harmful behaviors for jailbreaking evaluation.

    Reference: [@chao2024jailbreakbench]

    Content Warning: This dataset contains prompts aimed at provoking harmful responses
    and may contain offensive content. Users should check with their legal department
    before using these prompts against production LLMs.
    """

    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "disinformation": [HarmCategory.INFO_INTEGRITY],
        "economic harm": [HarmCategory.SCAMS],
        "expert advice": [HarmCategory.HEALTH_DIAGNOSIS, HarmCategory.LEGAL_ADVICE, HarmCategory.FINANCIAL_ADVICE],
        "fraud/deception": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        "government decision-making": [HarmCategory.HIGH_RISK_GOVERNMENT],
        "harassment/discrimination": [
            HarmCategory.HARASSMENT,
            HarmCategory.HATE_SPEECH,
            HarmCategory.REPRESENTATIONAL,
        ],
        "malware/hacking": [HarmCategory.MALWARE],
        "physical harm": [HarmCategory.VIOLENT_CONTENT, HarmCategory.COORDINATION_HARM],
        "privacy": [HarmCategory.PPI],
        "sexual/adult content": [HarmCategory.SEXUAL_CONTENT],
    }

    _AUTHORS = [
        "Patrick Chao",
        "Edoardo Debenedetti",
        "Alexander Robey",
        "Maksym Andriushchenko",
        "Francesco Croce",
        "Vikash Sehwag",
        "Edgar Dobriban",
        "Nicolas Flammarion",
        "George J. Pappas",
        "Florian Tramer",
        "Hamed Hassani",
        "Eric Wong",
    ]

    _GROUPS = [
        "University of Pennsylvania",
        "ETH Zurich",
        "EPFL",
        "Sony AI",
    ]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "small"  # 100 harmful behaviors across 10 categories
    tags: frozenset[str] = frozenset({"safety", "jailbreak"})

    def __init__(
        self,
        *,
        source: str = "JailbreakBench/JBB-Behaviors",
        split: str = "behaviors",
    ) -> None:
        """
        Initialize the JBB-Behaviors dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "JailbreakBench/JBB-Behaviors".
            split: Dataset split to load. Defaults to "behaviors".
        """
        self.source = source
        self.split = split

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "jbb_behaviors"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch JBB-Behaviors dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the JBB behaviors with harm_categories set.

        Raises:
            ValueError: If the dataset is empty after processing.
            Exception: If the dataset cannot be loaded or processed.
        """
        try:
            logger.info(f"Loading JBB-Behaviors dataset from {self.source}")

            # Load from HuggingFace
            # Note: JBB-Behaviors has 'harmful' and 'benign' splits
            data = await self._fetch_from_huggingface_async(
                dataset_name=self.source,
                config=self.split,
                split="harmful",
                cache=cache,
            )

            description = (
                "A dataset of harmful behaviors for jailbreaking evaluation from JailbreakBench. "
                "Contains behaviors designed to test AI safety measures."
            )

            seed_prompts: list[SeedUnion] = []

            for item in data:
                # Extract the required fields
                behavior = item.get("Behavior", "").strip()
                category = item.get("Category", "")

                if not behavior:
                    logger.warning("[JBB-Behaviors] Skipping item with empty behavior field")
                    continue

                standardized_categories = self._standardize_harm_categories(
                    category,
                    alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                )

                # Create SeedPrompt object with all metadata
                seed_prompt = SeedPrompt(
                    value=behavior,
                    data_type="text",
                    name="JBB-Behaviors",
                    dataset_name=self.dataset_name,
                    harm_categories=standardized_categories,
                    description=description,
                    authors=self._AUTHORS,
                    groups=self._GROUPS,
                    source=self.source,
                    metadata={
                        "jbb_category": category,
                        "original_source": "JailbreakBench",
                    },
                )

                seed_prompts.append(seed_prompt)

            if not seed_prompts:
                raise ValueError("SeedDataset cannot be empty.")

            logger.info(f"Successfully loaded {len(seed_prompts)} behaviors from JBB-Behaviors dataset")

            return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

        except Exception as e:
            logger.error(f"Failed to load JBB-Behaviors dataset: {str(e)}")
            raise Exception(f"Error loading JBB-Behaviors dataset: {str(e)}") from e
