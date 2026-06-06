# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import warnings
from typing import Any

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

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
        - [@zhang2024cbtbench]
    """

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "small"  # 20 core_fine_seed therapy seeds (default config)
    tags: frozenset[str] = frozenset({"safety", "medical"})

    def __init__(
        self,
        *,
        source: str = "Psychotherapy-LLM/CBT-Bench",
        config: str = "core_fine_seed",
        split: str | None = None,
    ) -> None:
        """
        Initialize the CBT-Bench dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "Psychotherapy-LLM/CBT-Bench".
            config: Dataset configuration/subset to load. Defaults to "core_fine_seed".
            split: **Deprecated.** Every config of ``Psychotherapy-LLM/CBT-Bench`` publishes
                only the ``"train"`` split, so this kwarg has no effect. It will be removed
                in v0.16.0.
        """
        if split is not None:
            warnings.warn(
                "'split' is deprecated and will be removed in v0.16.0. "
                "Every config of Psychotherapy-LLM/CBT-Bench publishes only the 'train' "
                "split, so this kwarg has no effect.",
                DeprecationWarning,
                stacklevel=2,
            )
        self.source = source
        self.config = config

    @property
    @override
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "cbt_bench"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
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

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.source,
            config=self.config,
            split="train",
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
        groups = [
            "University of Texas at Dallas",
            "University of California, Santa Barbara",
            "University of Pittsburgh",
            "Princeton University",
            "Carnegie Mellon University",
        ]
        description = (
            "CBT-Bench is a benchmark designed to evaluate the proficiency of Large Language Models "
            "in assisting Cognitive Behavioral Therapy (CBT). The dataset covers basic CBT knowledge, "
            "cognitive model understanding, and therapeutic response generation."
        )

        seed_prompts: list[SeedUnion] = []

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
                groups=groups,
                metadata=metadata,
            )

            seed_prompts.append(seed_prompt)

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty.")

        logger.info(f"Successfully loaded {len(seed_prompts)} examples from CBT-Bench dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
