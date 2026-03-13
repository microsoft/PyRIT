# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _JBBBehaviorsDataset(_RemoteDatasetLoader):
    """
    Loader for the JailbreakBench JBB-Behaviors dataset.

    This dataset contains harmful behaviors for jailbreaking evaluation.

    Reference: https://arxiv.org/abs/2404.01318

    Content Warning: This dataset contains prompts aimed at provoking harmful responses
    and may contain offensive content. Users should check with their legal department
    before using these prompts against production LLMs.
    """

    def __init__(
        self,
        *,
        source: str = "JailbreakBench/JBB-Behaviors",
        split: str = "behaviors",
    ):
        """
        Initialize the JBB-Behaviors dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "JailbreakBench/JBB-Behaviors".
            split: Dataset split to load. Defaults to "behaviors".
        """
        self.source = source
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "jbb_behaviors"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
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
            data = await self._fetch_from_huggingface(
                dataset_name=self.source,
                config=self.split,
                split="harmful",
                cache=cache,
            )

            # Define common metadata
            common_metadata = {
                "dataset_name": self.dataset_name,
                "authors": ["JailbreakBench Team"],
                "description": (
                    "A dataset of harmful behaviors for jailbreaking evaluation from JailbreakBench. "
                    "Contains behaviors designed to test AI safety measures."
                ),
                "source": self.source,
                "data_type": "text",
                "name": "JBB-Behaviors",
            }

            seed_prompts = []

            for item in data:
                # Extract the required fields
                behavior = item.get("Behavior", "").strip()
                category = item.get("Category", "")

                if not behavior:
                    logger.warning("[JBB-Behaviors] Skipping item with empty behavior field")
                    continue

                # Map JBB categories to PyRIT harm categories
                harm_categories = self._map_jbb_category_to_harm_category(category)

                # Create SeedPrompt object with all metadata
                seed_prompt = SeedPrompt(
                    value=behavior,
                    harm_categories=harm_categories,
                    groups=[category] if category else [],
                    metadata={
                        "jbb_category": category,
                        "original_source": "JailbreakBench",
                    },
                    **common_metadata,  # type: ignore[arg-type]
                )

                seed_prompts.append(seed_prompt)

            if not seed_prompts:
                raise ValueError("SeedDataset cannot be empty.")

            logger.info(f"Successfully loaded {len(seed_prompts)} behaviors from JBB-Behaviors dataset")

            return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

        except Exception as e:
            logger.error(f"Failed to load JBB-Behaviors dataset: {str(e)}")
            raise Exception(f"Error loading JBB-Behaviors dataset: {str(e)}") from e

    def _map_jbb_category_to_harm_category(self, jbb_category: str) -> list[str]:
        """
        Map JBB categories to PyRIT harm categories.

        Args:
            jbb_category: The category from the JBB dataset.

        Returns:
            List of corresponding PyRIT harm categories.
        """
        # Based on common harm taxonomies and JailbreakBench paper categories
        category_mapping = {
            # Violence and harm
            "violence": ["violence"],
            "physical_harm": ["violence", "physical_harm"],
            "weapons": ["violence", "weapons"],
            "terrorism": ["violence", "terrorism"],
            # Hate and harassment
            "hate": ["hate"],
            "harassment": ["harassment"],
            "bullying": ["harassment", "bullying"],
            "discrimination": ["discrimination"],
            "bias": ["bias", "discrimination"],
            # Sexual content
            "sexual": ["sexual"],
            "adult_content": ["sexual"],
            "non_consensual": ["sexual", "non_consensual"],
            # Self-harm and suicide
            "self_harm": ["self_harm"],
            "suicide": ["self_harm", "suicide"],
            "eating_disorders": ["self_harm", "eating_disorders"],
            # Criminal activities
            "criminal": ["criminal_planning"],
            "illegal": ["illegal_activity"],
            "fraud": ["criminal_planning", "fraud"],
            "theft": ["criminal_planning", "theft"],
            "cybercrime": ["criminal_planning", "cybercrime"],
            # Privacy and personal information
            "privacy": ["privacy"],
            "personal_info": ["privacy", "personal_information"],
            "doxxing": ["privacy", "doxxing"],
            # Deception and misinformation
            "deception": ["deception"],
            "misinformation": ["misinformation"],
            "conspiracy": ["misinformation", "conspiracy"],
            "fake_news": ["misinformation"],
            # Unethical behavior
            "unethical": ["unethical"],
            "manipulation": ["unethical", "manipulation"],
            "exploitation": ["unethical", "exploitation"],
            # Substance abuse
            "drugs": ["substance_abuse"],
            "alcohol": ["substance_abuse"],
            # Financial harm
            "financial_harm": ["financial_harm"],
            "scams": ["financial_harm", "scams"],
            # Child safety
            "child_abuse": ["child_safety"],
            "grooming": ["child_safety", "grooming"],
            # Government and political
            "government": ["government"],
            "political": ["political"],
            "election": ["political", "election"],
        }

        if not jbb_category or not jbb_category.strip():
            return ["unknown"]

        # Convert to lowercase for case-insensitive matching
        jbb_category_lower = jbb_category.lower().strip()

        # Try exact match first
        if jbb_category_lower in category_mapping:
            return category_mapping[jbb_category_lower]

        # Try partial matching for compound categories
        matched_categories = set()
        for key, harm_cats in category_mapping.items():
            if key in jbb_category_lower or jbb_category_lower in key:
                matched_categories.update(harm_cats)

        if matched_categories:
            return list(matched_categories)

        # Special handling for common patterns
        if any(term in jbb_category_lower for term in ["violent", "kill", "murder", "bomb"]):
            return ["violence"]
        if any(term in jbb_category_lower for term in ["hate", "racist", "sexist"]):
            return ["hate", "discrimination"]
        if any(term in jbb_category_lower for term in ["sexual", "porn", "nsfw"]):
            return ["sexual"]
        if any(term in jbb_category_lower for term in ["illegal", "crime", "criminal"]):
            return ["criminal_planning", "illegal_activity"]
        if any(term in jbb_category_lower for term in ["harm", "hurt", "damage"]):
            return ["violence", "harm"]

        # Default: use the original JBB category
        logger.info(f"[JBB-Behaviors] Using JBB category '{jbb_category}' as harm category")
        return [jbb_category_lower]
