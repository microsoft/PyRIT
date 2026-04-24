# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _ORBenchBaseDataset(_RemoteDatasetLoader):
    """
    Base loader for OR-Bench datasets from HuggingFace.

    Subclasses must set CONFIG, provide a dataset_name property, and a description.

    References:
        - https://huggingface.co/datasets/bench-llm/OR-Bench
        - [@cui2024orbench]
    License: CC BY 4.0

    Warning: This dataset contains prompts designed to test over-refusal behavior in LLMs,
    including potentially harmful and toxic content.
    """

    HF_DATASET_NAME: str = "bench-llm/OR-Bench"
    CONFIG: str
    DESCRIPTION: str

    def __init__(self, *, split: str = "train") -> None:
        """
        Initialize the OR-Bench dataset loader.

        Args:
            split: Dataset split to load. Defaults to "train".
        """
        self.split = split

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch OR-Bench dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the OR-Bench prompts.
        """
        logger.info(f"Loading OR-Bench dataset from {self.HF_DATASET_NAME} (config={self.CONFIG})")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            config=self.CONFIG,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Justin Cui",
            "Wei-Lin Chiang",
            "Ion Stoica",
            "Cho-Jui Hsieh",
        ]
        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["UCLA", "UC Berkeley"]

        seed_prompts = [
            SeedPrompt(
                value=item["prompt"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[item["category"]] if item.get("category") else [],
                description=self.DESCRIPTION,
                source=source_url,
                authors=authors,
                groups=groups,
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from OR-Bench dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)


class _ORBench80KDataset(_ORBenchBaseDataset):
    """
    Loader for the OR-Bench 80K dataset.

    Contains ~80k over-refusal prompts categorized into 10 common rejection categories.
    This is the main comprehensive benchmark for evaluating LLM over-refusal behavior.
    """

    CONFIG: str = "or-bench-80k"
    DESCRIPTION: str = (
        "OR-Bench 80K contains ~80k over-refusal prompts categorized into 10 rejection "
        "categories. This is the main comprehensive benchmark for evaluating LLM over-refusal."
    )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "or_bench_80k"


class _ORBenchHardDataset(_ORBenchBaseDataset):
    """
    Loader for the OR-Bench Hard-1K dataset.

    Contains ~1k challenging safe prompts that commonly trigger over-refusal in LLMs.
    These are prompts that models should be able to answer without refusing.
    """

    CONFIG: str = "or-bench-hard-1k"
    DESCRIPTION: str = (
        "OR-Bench Hard-1K contains ~1k challenging safe prompts that commonly trigger "
        "over-refusal in language models. These prompts should be answerable without refusing."
    )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "or_bench_hard"


class _ORBenchToxicDataset(_ORBenchBaseDataset):
    """
    Loader for the OR-Bench Toxic dataset.

    Contains toxic prompts that language models should correctly refuse.
    Used as a contrast set to evaluate whether models can distinguish
    genuinely harmful prompts from safe ones.
    """

    CONFIG: str = "or-bench-toxic"
    DESCRIPTION: str = (
        "OR-Bench Toxic contains toxic prompts that language models should correctly refuse. "
        "Used as a contrast set to evaluate refusal calibration."
    )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "or_bench_toxic"
