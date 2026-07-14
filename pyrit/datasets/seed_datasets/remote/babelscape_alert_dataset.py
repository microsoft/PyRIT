# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import TYPE_CHECKING, Literal

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class _BabelscapeAlertDataset(_RemoteDatasetLoader):
    """
    Loader for the Babelscape/ALERT dataset.

    This dataset consists of two categories:
    - 'alert': 15k red teaming prompts
    - 'alert_adversarial': 30k adversarial red teaming prompts

    Reference: [@tedeschi2024alert]
    """

    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "crime_cyber": [HarmCategory.MALWARE, HarmCategory.COORDINATION_HARM],
        "crime_injury": [HarmCategory.VIOLENT_CONTENT],
        "crime_kidnapp": [HarmCategory.VIOLENT_THREATS, HarmCategory.COORDINATION_HARM],
        "crime_other": [HarmCategory.COORDINATION_HARM],
        "crime_privacy": [HarmCategory.PPI],
        "crime_propaganda": [HarmCategory.INFO_INTEGRITY],
        "crime_tax": [HarmCategory.COORDINATION_HARM],
        "crime_theft": [HarmCategory.COORDINATION_HARM],
        "hate_body": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_disabled": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_ethnic": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_lgbtq+": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_other": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_poor": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_religion": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "hate_women": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "self_harm_other": [HarmCategory.SELF_HARM],
        "self_harm_suicide": [HarmCategory.SUICIDE, HarmCategory.SELF_HARM],
        "self_harm_thin": [HarmCategory.EATING_DISORDERS, HarmCategory.SELF_HARM],
        "sex_harrasment": [HarmCategory.HARASSMENT, HarmCategory.SEXUAL_SOLICITATION],
        "sex_other": [HarmCategory.SEXUAL_CONTENT],
        "sex_porn": [HarmCategory.SEXUAL_CONTENT],
        "substance_alcohol": [HarmCategory.DRUG_USE, HarmCategory.REGULATED_GOODS],
        "substance_cannabis": [HarmCategory.DRUG_USE, HarmCategory.REGULATED_GOODS],
        "substance_drug": [HarmCategory.DRUG_USE],
        "substance_other": [HarmCategory.DRUG_USE],
        "substance_tobacco": [HarmCategory.DRUG_USE, HarmCategory.REGULATED_GOODS],
        "weapon_biological": [HarmCategory.CBRN],
        "weapon_chemical": [HarmCategory.CBRN],
        "weapon_firearm": [HarmCategory.REGULATED_GOODS],
        "weapon_other": [HarmCategory.REGULATED_GOODS],
        "weapon_radioactive": [HarmCategory.CBRN],
    }

    _AUTHORS = [
        "Simone Tedeschi",
        "Felix Friedrich",
        "Patrick Schramowski",
        "Kristian Kersting",
        "Roberto Navigli",
        "Huu Nguyen",
        "Bo Li",
    ]

    _GROUPS = [
        "Sapienza University of Rome",
        "Babelscape",
        "TU Darmstadt",
        "Hessian.AI",
        "DFKI",
        "Ontocord.AI",
        "University of Chicago",
        "University of Illinois Urbana-Champaign",
    ]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "huge"  # 30968 prompts (default config)
    tags: frozenset[str] = frozenset({"default", "safety", "jailbreak"})

    def __init__(
        self,
        *,
        source: str = "Babelscape/ALERT",
        category: Literal["alert", "alert_adversarial"] | None = "alert_adversarial",
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
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "babelscape_alert"

    @override
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
            data = await self._fetch_from_huggingface_async(
                dataset_name=self.source,
                config=category_name,
                split="test",
                cache=cache,
            )
            prompts.extend((item["prompt"], item["category"]) for item in data)

        seed_prompts: list[SeedUnion] = [
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
                authors=self._AUTHORS,
                groups=self._GROUPS,
            )
            for prompt, category in prompts
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Babelscape Alert dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
