# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class _TDC23RedteamingDataset(_RemoteDatasetLoader):
    """
    Loader for the TDC23-RedTeaming dataset.

    This dataset contains 100 prompts aimed at generating harmful content across multiple
    harm categories related to fairness, misinformation, dangerous and criminal activities,
    violence, etc. in the style of writing narratives.

    Reference: [@mazeika2023tdc]
    """

    _AUTHORS = [
        "Mantas Mazeika",
        "Andy Zou",
        "Norman Mu",
        "Long Phan",
        "Zifan Wang",
        "Chunru Yu",
        "Adam Khoja",
        "Fengqing Jiang",
        "Aidan O'Gara",
        "Ellie Sakhaee",
        "Zhen Xiang",
        "Arezoo Rajabi",
        "Dan Hendrycks",
        "Radha Poovendran",
        "Bo Li",
        "David Forsyth",
    ]

    _GROUPS = ["Center for AI Safety"]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "small"  # 100 narrative-style harmful prompts
    tags: frozenset[str] = frozenset({"safety", "jailbreak"})

    def __init__(
        self,
        *,
        source: str = "walledai/TDC23-RedTeaming",
    ) -> None:
        """
        Initialize the TDC23-RedTeaming dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "walledai/TDC23-RedTeaming".
        """
        self.source = source

    @property
    @override
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "tdc23_redteaming"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch TDC23-RedTeaming dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the red-teaming prompts.
        """
        logger.info(f"Loading TDC23-RedTeaming dataset from {self.source}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.source,
            config="default",
            split="train",
            cache=cache,
        )

        seed_prompts: list[SeedUnion] = [
            SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                description=(
                    "TDC23-RedTeaming dataset from HuggingFace, created by Walled AI. "
                    "Contains 100 prompts aimed at generating harmful content across multiple harm categories "
                    "related to fairness, misinformation, dangerous and criminal activities, violence, etc. "
                    "in the style of writing narratives."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
                authors=self._AUTHORS,
                groups=self._GROUPS,
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from TDC23-RedTeaming dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
