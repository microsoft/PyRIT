# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from typing import Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _HiXSTestDataset(_RemoteDatasetLoader):
    """
    Loader for the HiXSTest (Hindi Exaggerated-Safety Test) dataset from HuggingFace.

    HiXSTest is a manually-curated set of 50 exaggerated-safety prompts in Hindi (with
    English translations), companion to SGXSTest. It tests whether language models exhibit
    exaggerated-safety behavior (refusing benign prompts whose harmful interpretation is
    not warranted in Hindi cultural context).

    Each example contains:
        - prompt: the prompt text in Hindi (the primary value used by the SeedPrompt)
        - english_prompt: English translation of the prompt
        - label: "safe" or "unsafe"
        - category: the type of exaggerated-safety pattern being tested (e.g. "homonyms")

    Note: This is a gated dataset on HuggingFace. You must accept the terms at
    https://huggingface.co/datasets/walledai/HiXSTest before use, and provide a
    HuggingFace token (either via the ``token`` parameter or the
    ``HUGGINGFACE_TOKEN`` environment variable).

    References:
        - https://huggingface.co/datasets/walledai/HiXSTest
        - [@gupta2024walledeval]
    License: Apache-2.0
    """

    HF_DATASET_NAME: str = "walledai/HiXSTest"

    # Class-level dataset metadata for SeedDatasetMetadata discovery
    modalities: list[str] = ["text"]
    size: str = "small"  # 50 seeds
    tags: set[str] = {"default", "safety", "multilingual"}

    def __init__(
        self,
        *,
        split: str = "train",
        token: Optional[str] = None,
    ) -> None:
        """
        Initialize the HiXSTest dataset loader.

        Args:
            split: Dataset split to load. Defaults to "train" (the only split).
            token: Hugging Face authentication token. If not provided, reads from the
                ``HUGGINGFACE_TOKEN`` environment variable.
        """
        self.split = split
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "hixstest"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HiXSTest dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the HiXSTest prompts. The Hindi
                prompt is stored as the ``value``; the English translation, label,
                and category are stored in ``metadata``.
        """
        logger.info(f"Loading HiXSTest dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            split=self.split,
            cache=cache,
            token=self.token,
        )

        authors = [
            "Prannaya Gupta",
            "Le Qi Yau",
            "Hao Han Low",
            "I-Shiang Lee",
            "Hugo Maximus Lim",
            "Yu Xin Teoh",
            "Jia Hng Koh",
            "Dar Win Liew",
            "Rishabh Bhardwaj",
            "Rajat Bhardwaj",
            "Soujanya Poria",
        ]
        description = (
            "HiXSTest contains 50 manually-curated exaggerated-safety prompts in Hindi "
            "(with English translations), companion to SGXSTest. It tests whether language "
            "models exhibit exaggerated-safety behavior in a Hindi cultural context. "
            "Introduced in 'WalledEval: A Comprehensive Safety Evaluation Toolkit for "
            "Large Language Models' (2024)."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["Walled AI", "DeCLaRe Lab, Singapore University of Technology and Design"]

        seed_prompts = [
            SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[item["category"]] if item.get("category") else [],
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata={
                    "english_prompt": item.get("english_prompt", ""),
                    "label": item.get("label", ""),
                    "category": item.get("category", ""),
                    "language": "hi",
                },
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from HiXSTest dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
