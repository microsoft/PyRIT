# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import re

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _SaladBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the SALAD-Bench dataset from HuggingFace.

    SALAD-Bench is a hierarchical and comprehensive safety benchmark for large language models.
    It organizes harmful questions into 6 domains, 16 tasks, and 65+ categories,
    totaling about 30k questions. It covers QA, multiple choice, attack-enhanced,
    and defense-enhanced variants.

    References:
        - https://huggingface.co/datasets/walledai/SaladBench
        - [@li2024saladbench]
        - https://github.com/OpenSafetyLab/SALAD-BENCH
    License: Apache 2.0

    Warning: This dataset contains harmful and unsafe content designed for safety evaluation.
    """

    HF_DATASET_NAME: str = "walledai/SaladBench"

    def __init__(
        self,
        *,
        config: str = "prompts",
        split: str = "base",
    ) -> None:
        """
        Initialize the SALAD-Bench dataset loader.

        Args:
            config: Dataset configuration. Defaults to "prompts".
            split: Dataset split to load. One of "base", "attackEnhanced", "defenseEnhanced".
                Defaults to "base".
        """
        self.config = config
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "salad_bench"

    @staticmethod
    def _parse_category(category: str) -> str:
        """
        Strip leading identifier like 'O6: ' from a category string.

        Args:
            category (str): The category string to parse.

        Returns:
            str: The category string without the leading identifier.
        """
        return re.sub(r"^O\d+:\s*", "", category)

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch SALAD-Bench dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SALAD-Bench prompts.
        """
        logger.info(f"Loading SALAD-Bench dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            config=self.config,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Lijun Li",
            "Bowen Dong",
            "Ruohui Wang",
            "Xuhao Hu",
            "Wangmeng Zuo",
            "Dahua Lin",
            "Yu Qiao",
            "Jing Shao",
        ]
        description = (
            "SALAD-Bench is a hierarchical and comprehensive safety benchmark for large language "
            "models (ACL 2024). It contains about 30k questions organized into 6 domains, 16 tasks, "
            "and 65+ categories, with base, attack-enhanced, and defense-enhanced variants."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = [
            "Shanghai Artificial Intelligence Laboratory",
            "Harbin Institute of Technology",
            "Beijing Institute of Technology",
            "Chinese University of Hong Kong",
            "The Hong Kong Polytechnic University",
        ]

        seed_prompts = [
            SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[self._parse_category(c) for c in item["categories"]],
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata={"original_source": src} if (src := item.get("source")) else {},
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from SALAD-Bench dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
