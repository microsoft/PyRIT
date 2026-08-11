# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _BeaverTailsDataset(_RemoteDatasetLoader):
    """
    Loader for the BeaverTails dataset from HuggingFace.

    BeaverTails contains 330k+ entries annotated across 14 harm categories.
    It is widely used for safety alignment research. This loader extracts only the
    prompts (not the responses) and filters to unsafe entries by default.

    References:
        - https://huggingface.co/datasets/PKU-Alignment/BeaverTails
        - [@ji2023beavertails]
        - https://github.com/PKU-Alignment/beavertails
    License: CC BY-NC 4.0

    Warning: This dataset contains unsafe and potentially harmful content. Consult your
    legal department before using these prompts for testing.
    """

    HF_DATASET_NAME: str = "PKU-Alignment/BeaverTails"
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "aiding_and_abetting": [HarmCategory.COORDINATION_HARM],
        "animal_abuse": [HarmCategory.OTHER],
        "banned_substance": [HarmCategory.DRUG_USE, HarmCategory.REGULATED_GOODS],
        "child_abuse": [HarmCategory.GROOMING, HarmCategory.SEXUAL_CONTENT, HarmCategory.CHILD_LEAKAGE],
        "controversial_topics": [HarmCategory.OTHER],
        "discrimination": [HarmCategory.REPRESENTATIONAL, HarmCategory.HATE_SPEECH],
        "drug_abuse": [HarmCategory.DRUG_USE],
        "financial_crime": [HarmCategory.SCAMS, HarmCategory.COORDINATION_HARM],
        "hate_speech": [HarmCategory.HATE_SPEECH],
        "incitement": [HarmCategory.VIOLENT_THREATS],
        "injustice": [HarmCategory.REPRESENTATIONAL, HarmCategory.HATE_SPEECH],
        "laws_and_safety": [HarmCategory.INFO_INTEGRITY],
        "misinformation_regarding_ethics": [HarmCategory.INFO_INTEGRITY],
        "non_violent_unethical_behavior": [HarmCategory.OTHER],
        "offensive_language": [HarmCategory.HATE_SPEECH],
        "organized_crime": [HarmCategory.COORDINATION_HARM],
        "politics": [HarmCategory.OTHER],
        "privacy_violation": [HarmCategory.PPI],
        "property_crime": [HarmCategory.COORDINATION_HARM],
        "self_harm": [HarmCategory.SELF_HARM],
        "sexually_explicit": [HarmCategory.SEXUAL_CONTENT],
        "stereotype": [HarmCategory.REPRESENTATIONAL, HarmCategory.HATE_SPEECH],
        "terrorism": [HarmCategory.VIOLENT_EXTREMISM],
        "theft": [HarmCategory.COORDINATION_HARM],
        "violence": [HarmCategory.VIOLENT_CONTENT, HarmCategory.VIOLENT_THREATS, HarmCategory.COORDINATION_HARM],
        "weapons": [HarmCategory.REGULATED_GOODS],
    }

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "huge"  # 166382 annotated prompt-response entries (default config)
    tags: frozenset[str] = frozenset({"default", "safety"})

    def __init__(
        self,
        *,
        split: str = "330k_train",
        unsafe_only: bool = True,
    ) -> None:
        """
        Initialize the BeaverTails dataset loader.

        Args:
            split: Dataset split to load. Defaults to "330k_train".
            unsafe_only: If True, only load entries marked as unsafe. Defaults to True.
        """
        self.split = split
        self.unsafe_only = unsafe_only

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "beaver_tails"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch BeaverTails dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the BeaverTails prompts.
        """
        logger.info(f"Loading BeaverTails dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Jiaming Ji",
            "Mickel Liu",
            "Juntao Dai",
            "Xuehai Pan",
            "Chi Zhang",
            "Ce Bian",
            "Boyuan Chen",
            "Ruiyang Sun",
            "Yizhou Wang",
            "Yaodong Yang",
        ]
        description = (
            "This seed dataset exposes only the prompts/questions derived from BeaverTails and does "
            "not include the corresponding answers. BeaverTails contains 330k+ entries annotated "
            "across 14 harm categories, designed for safety alignment research. Introduced in "
            "'BeaverTails: Towards Improved Safety Alignment of LLM via a Human-Preference Dataset' (2023)."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = [
            "Institute for Artificial Intelligence, Peking University",
            "Center on Frontiers of Computing Studies, School of Computer Science, Peking University",
        ]

        seed_prompts = []
        for item in data:
            if self.unsafe_only and item["is_safe"]:
                continue

            raw_harm_categories = [
                part.strip() for k, v in item["category"].items() if v for part in k.split(",") if part.strip()
            ]
            harm_categories = self._standardize_harm_categories(
                raw_harm_categories,
                alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
            )

            seed_prompts.append(
                SeedPrompt(
                    value=item["prompt"],
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=harm_categories,
                    description=description,
                    source=source_url,
                    authors=authors,
                    groups=groups,
                    metadata={
                        "beaver_tails_categories": ",".join(raw_harm_categories),
                        "beaver_tails_category_flags": json.dumps(item["category"], sort_keys=True),
                    },
                )
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from BeaverTails dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
