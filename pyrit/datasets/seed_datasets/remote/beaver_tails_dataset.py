# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from jinja2 import TemplateSyntaxError

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

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

    def __init__(
        self,
        *,
        split: str = "330k_train",
        unsafe_only: bool = True,
    ):
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
        """Return the dataset name."""
        return "beaver_tails"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch BeaverTails dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the BeaverTails prompts.
        """
        logger.info(f"Loading BeaverTails dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
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
        groups = ["Institute for Artificial Intelligence", "CFCS, School of Computer Science"]

        seed_prompts = []
        for item in data:
            if self.unsafe_only and item["is_safe"]:
                continue

            harm_categories = [k for k, v in item["category"].items() if v]

            try:
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
                    )
                )
            except TemplateSyntaxError:
                logger.warning("Skipping BeaverTails prompt due to Jinja2 template syntax error in prompt text")

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from BeaverTails dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
