# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _SGXSTestDataset(_RemoteDatasetLoader):
    """
    Loader for the SGXSTest (Singapore exaggerated-safety test) dataset from HuggingFace.

    SGXSTest contains 100 manually-curated safe/unsafe prompt pairs (200 prompts total) that
    test exaggerated-safety behavior of LLMs in a Singaporean cultural context. It adapts
    the 10 hazard categories of XSTest (Röttger et al. 2023): homonym, figurative language,
    safe targets, safe contexts, definitions, real discrimination / nonsense group, nonsense
    discrimination / real group, historical events, privacy (public), and privacy (fiction).

    References:
        - https://huggingface.co/datasets/walledai/SGXSTest
        - [@gupta2024walledeval]
    License: Apache-2.0

    Note: This is a gated dataset on HuggingFace. You must accept the terms at
    https://huggingface.co/datasets/walledai/SGXSTest before use, and provide a
    HuggingFace token (either via the ``token`` constructor argument or by setting
    the ``HUGGINGFACE_TOKEN`` environment variable).
    """

    HF_DATASET_NAME: str = "walledai/SGXSTest"

    # Metadata
    harm_categories: list[str] = [
        "definitions",
        "figurative language",
        "historical events",
        "homonym",
        "nonsense discrimination, real group",
        "privacy (fiction)",
        "privacy (public)",
        "real discrimination, nonsense group",
        "safe contexts",
        "safe targets",
    ]
    modalities: list[str] = ["text"]
    size: str = "medium"  # 200 prompts
    tags: set[str] = {"default", "safety", "multilingual_culture"}

    def __init__(
        self,
        *,
        split: str = "train",
        token: str | None = None,
    ) -> None:
        """
        Initialize the SGXSTest dataset loader.

        Args:
            split: Dataset split to load. Defaults to "train".
            token: Hugging Face authentication token. If not provided, reads from
                the HUGGINGFACE_TOKEN env var.
        """
        self.split = split
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "sgxstest"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch SGXSTest dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SGXSTest prompts. Each SeedPrompt's
            ``metadata`` dict contains ``label`` ("safe" or "unsafe") and ``category``
            (one of the 10 hazard categories), enabling filtering after load.
        """
        logger.info(f"Loading SGXSTest dataset from {self.HF_DATASET_NAME}")

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
            "SGXSTest contains 100 manually-curated safe/unsafe prompt pairs (200 prompts total) "
            "testing exaggerated-safety behavior of LLMs in a Singaporean cultural context. Adapts "
            "the 10 hazard categories of XSTest (Röttger et al. 2023). Introduced in 'WalledEval: A "
            "Comprehensive Safety Evaluation Toolkit for Large Language Models' (Gupta et al. 2024)."
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
                    "label": item["label"],
                    "category": item["category"],
                },
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from SGXSTest dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
