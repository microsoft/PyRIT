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


class _LLMLatentAdversarialTrainingDataset(_RemoteDatasetLoader):
    """
    Loader for the LLM-LAT harmful dataset.

    This dataset contains prompts used to assess and analyze harmful behaviors
    in large language models.

    Reference: [@sheshadri2024lat]
    """

    _AUTHORS = [
        "Abhay Sheshadri",
        "Aidan Ewart",
        "Phillip Guo",
        "Aengus Lynch",
        "Cindy Wu",
        "Vivek Hebbar",
        "Henry Sleight",
        "Asa Cooper Stickland",
        "Ethan Perez",
        "Dylan Hadfield-Menell",
        "Stephen Casper",
    ]

    _GROUPS = [
        "Georgia Institute of Technology",
        "University of Bristol",
        "University of Maryland",
        "University College London",
        "MATS",
        "Astra",
        "New York University",
        "Anthropic",
        "MIT CSAIL",
    ]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 4948 harmful prompts
    tags: frozenset[str] = frozenset({"default", "safety", "jailbreak"})

    def __init__(
        self,
        *,
        source: str = "LLM-LAT/harmful-dataset",
    ) -> None:
        """
        Initialize the LLM-LAT harmful dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "LLM-LAT/harmful-dataset".
        """
        self.source = source

    @property
    @override
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "llm_lat_harmful"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch LLM-LAT harmful dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the harmful prompts.
        """
        logger.info(f"Loading LLM-LAT harmful dataset from {self.source}")

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
                description="This dataset contains prompts used to assess and analyze harmful behaviors in llm",
                source=f"https://huggingface.co/datasets/{self.source}",
                authors=self._AUTHORS,
                groups=self._GROUPS,
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from LLM-LAT harmful dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
