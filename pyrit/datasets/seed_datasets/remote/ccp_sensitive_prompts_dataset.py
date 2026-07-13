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


class _CCPSensitivePromptsDataset(_RemoteDatasetLoader):
    """
    Loader for the CCP Sensitive Prompts dataset.

    This dataset contains prompts covering topics sensitive to the Chinese Communist Party (CCP).
    These prompts are likely to be censored by certain models.

    Reference: [@promptfoo2025ccp]
    """

    _AUTHORS = ["Ian Webster"]

    _GROUPS = ["Promptfoo"]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 1360 censorship-sensitive prompts (single-language Mandarin)
    tags: frozenset[str] = frozenset({"safety", "multilingual"})

    def __init__(
        self,
        *,
        source: str = "promptfoo/CCP-sensitive-prompts",
    ) -> None:
        """
        Initialize the CCP-sensitive prompts dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "promptfoo/CCP-sensitive-prompts".
        """
        self.source = source

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "ccp_sensitive_prompts"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch CCP-sensitive prompts dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing CCP-sensitive prompts.
        """
        logger.info(f"Loading CCP-sensitive prompts dataset from {self.source}")

        # Load from HuggingFace
        data = await self._fetch_from_huggingface_async(
            dataset_name=self.source,
            split="train",
            cache=cache,
        )

        seed_prompts: list[SeedUnion] = [
            SeedPrompt(
                value=row["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[row["subject"]],
                description="Prompts covering topics sensitive to the CCP.",
                authors=self._AUTHORS,
                groups=self._GROUPS,
                source=f"https://huggingface.co/datasets/{self.source}",
            )
            for row in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from CCP Sensitive Prompts dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
