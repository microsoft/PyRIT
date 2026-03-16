# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _CBTBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the CBT-Bench dataset from HuggingFace.

    CBT-Bench is a benchmark designed to evaluate the proficiency of Large Language Models
    in assisting Cognitive Behavioral Therapy (CBT). The dataset contains psychotherapy case
    scenarios with client situations, thoughts, and core belief classifications.

    The dataset is organized into multiple configurations covering basic CBT knowledge,
    cognitive model understanding, and therapeutic response generation.

    References:
        - https://huggingface.co/datasets/Psychotherapy-LLM/CBT-Bench
        - https://arxiv.org/abs/2410.13218
    """

    def __init__(
        self,
        *,
        source: str = "Psychotherapy-LLM/CBT-Bench",
        config: str = "core_fine_seed",
        split: str = "train",
    ):
        """
        Initialize the CBT-Bench dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "Psychotherapy-LLM/CBT-Bench".
            config: Dataset configuration/subset to load. Defaults to "core_fine_seed".
            split: Dataset split to load. Defaults to "train".
        """
        self.source = source
        self.config = config
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "cbt_bench"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch CBT-Bench dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing CBT-Bench examples.

        Raises:
            ValueError: If the dataset is empty after processing.
            Exception: If the dataset cannot be loaded or processed.
        """
        logger.info(f"Loading CBT-Bench dataset from {self.source} (config={self.config})")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            config=self.config,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Mian Zhang",
            "Xianjun Yang",
            "Xinlu Zhang",
            "Travis Labrum",
            "Jamie C Chiu",
            "Shaun M Eack",
            "Fei Fang",
            "William Yang Wang",
            "Zhiyu Zoey Chen",
        ]
        description = (
            "CBT-Bench is a benchmark designed to evaluate the proficiency of Large Language Models "
            "in assisting Cognitive Behavioral Therapy (CBT). The dataset covers basic CBT knowledge, "
            "cognitive model understanding, and therapeutic response generation."
        )

        seed_prompts = []

        for item in data:
            situation = item.get("situation", "").strip()
            thoughts = item.get("thoughts", "").strip()

            # Combine situation and thoughts as the prompt value
            if situation and thoughts:
                value = f"Situation: {situation}\n\nThoughts: {thoughts}"
            elif situation:
                value = situation
            elif thoughts:
                value = thoughts
            else:
                logger.warning("[CBT-Bench] Skipping item with no situation or thoughts")
                continue

            # Extract core beliefs for metadata
            core_beliefs = item.get("core_belief_fine_grained", [])

            metadata: dict[str, Any] = {
                "config": self.config,
            }

            if core_beliefs:
                metadata["core_belief_fine_grained"] = core_beliefs

            seed_prompt = SeedPrompt(
                value=value,
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=["psycho-social harms"],
                description=description,
                source=f"https://huggingface.co/datasets/{self.source}",
                authors=authors,
                metadata=metadata,
            )

            seed_prompts.append(seed_prompt)

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty.")

        logger.info(f"Successfully loaded {len(seed_prompts)} examples from CBT-Bench dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
