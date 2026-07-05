# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal, Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _BabelscapeAlertDataset(_RemoteDatasetLoader):
    """
    Loader for the Babelscape/ALERT dataset.

    This dataset consists of two categories:
    - 'alert': 15k red teaming prompts
    - 'alert_adversarial': 30k adversarial red teaming prompts

    Reference: [@tedeschi2024alert]
    """

    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[str]] = {
        "crime_cyber": ["MALWARE", "COORDINATION_HARM"],
        "crime_injury": ["VIOLENT_CONTENT"],
        "crime_kidnapp": ["VIOLENT_THREATS", "COORDINATION_HARM"],
        "crime_other": ["COORDINATION_HARM"],
        "crime_privacy": ["PPI"],
        "crime_propaganda": ["INFO_INTEGRITY"],
        "crime_tax": ["COORDINATION_HARM"],
        "crime_theft": ["COORDINATION_HARM"],
        "hate_body": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_disabled": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_ethnic": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_lgbtq+": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_other": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_poor": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_religion": ["HATESPEECH", "REPRESENTATIONAL"],
        "hate_women": ["HATESPEECH", "REPRESENTATIONAL"],
        "self_harm_other": ["SELF_HARM"],
        "self_harm_suicide": ["SUICIDE", "SELF_HARM"],
        "self_harm_thin": ["EATING_DISORDERS", "SELF_HARM"],
        "sex_harrasment": ["HARASSMENT", "SEXUAL_SOLICITATION"],
        "sex_other": ["SEXUAL_CONTENT"],
        "sex_porn": ["SEXUAL_CONTENT"],
        "substance_alcohol": ["DRUG_USE", "REGULATED_GOODS"],
        "substance_cannabis": ["DRUG_USE", "REGULATED_GOODS"],
        "substance_drug": ["DRUG_USE"],
        "substance_other": ["DRUG_USE"],
        "substance_tobacco": ["DRUG_USE", "REGULATED_GOODS"],
        "weapon_biological": ["CBRN"],
        "weapon_chemical": ["CBRN"],
        "weapon_firearm": ["REGULATED_GOODS"],
        "weapon_other": ["REGULATED_GOODS"],
        "weapon_radioactive": ["CBRN"],
    }

    def __init__(
        self,
        *,
        source: str = "Babelscape/ALERT",
        category: Optional[Literal["alert", "alert_adversarial"]] = "alert_adversarial",
    ) -> None:
        """
        Initialize the Babelscape ALERT dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "Babelscape/ALERT".
            category: The dataset category. "alert", "alert_adversarial", or None for both.
                Defaults to "alert_adversarial".

        Raises:
            ValueError: If an invalid category is provided.
        """
        self.source = source
        self.category = category

        if category is not None and category not in ["alert_adversarial", "alert"]:
            raise ValueError(f"Invalid Parameter: {category}. Expected 'alert_adversarial', 'alert', or None")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "babelscape_alert"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Babelscape ALERT dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the ALERT prompts.
        """
        logger.info(f"Loading Babelscape ALERT dataset from {self.source}")

        # Determine which categories to load
        data_categories = ["alert_adversarial", "alert"] if self.category is None else [self.category]

        prompts: list[tuple[str, str]] = []
        for category_name in data_categories:
            data = await self._fetch_from_huggingface(
                dataset_name=self.source,
                config=category_name,
                split="test",
                cache=cache,
            )
            prompts.extend((item["prompt"], item["category"]) for item in data)

        seed_prompts = [
            SeedPrompt(
                value=prompt,
                harm_categories=self._standardize_harm_categories(
                    category,
                    alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                ),
                data_type="text",
                dataset_name=self.dataset_name,
                description=(
                    "ALERT by Babelscape is a dataset that consists of two different categories, "
                    "'alert' with 15k red teaming prompts, and 'alert_adversarial' with 30k adversarial "
                    "red teaming prompts."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
                metadata={"category": category},
            )
            for prompt, category in prompts
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Babelscape Alert dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
