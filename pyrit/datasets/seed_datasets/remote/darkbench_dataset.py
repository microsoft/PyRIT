# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt


class _DarkBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the DarkBench dataset from HuggingFace.

    DarkBench is a comprehensive benchmark designed to detect dark design patterns
    in large language models (LLMs). Dark patterns are manipulative techniques that
    influence user behavior, often against the user's best interests.

    The benchmark comprises 660 prompts across six categories of dark patterns.

    References:
        - https://huggingface.co/datasets/apart/darkbench
        - https://darkbench.ai/
        - https://openreview.net/forum?id=odjMSBSWRt
    """

    def __init__(
        self,
        *,
        dataset_name: str = "apart/darkbench",
        config: str = "default",
        split: str = "train",
    ):
        """
        Initialize the DarkBench dataset loader.

        Args:
            dataset_name: HuggingFace dataset identifier. Defaults to "apart/darkbench".
            config: Dataset configuration. Defaults to "default".
            split: Dataset split to load. Defaults to "train".
        """
        self.hf_dataset_name = dataset_name
        self.config = config
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "dark_bench"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch DarkBench dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the DarkBench examples.

        Raises:
            ImportError: If datasets library is not installed.
            Exception: If the dataset cannot be loaded.
        """
        # Fetch from HuggingFace
        data = await self._fetch_from_huggingface(
            dataset_name=self.hf_dataset_name,
            config=self.config,
            split=self.split,
            cache=cache,
            data_files="darkbench.tsv",
        )

        # Process into SeedPrompts
        seed_prompts = [
            SeedPrompt(
                value=item["Example"],
                data_type="text",
                name="",
                dataset_name=self.dataset_name,
                harm_categories=[item["Deceptive Pattern"]],
                description=(
                    "DarkBench is a comprehensive benchmark designed to detect dark design patterns in large "
                    "language models (LLMs). Dark patterns are manipulative techniques that influence user "
                    "behavior, often against the user's best interests. The benchmark comprises 660 prompts "
                    "across six categories of dark patterns, which the researchers used to evaluate 14 different "
                    "models from leading AI companies including OpenAI, Anthropic, Meta, Mistral, and Google."
                ),
                source="https://huggingface.co/datasets/apart/darkbench",
                authors=[
                    "Esben Kran",
                    "Jord Nguyen",
                    "Akash Kundu",
                    "Sami Jawhar",
                    "Jinsuk Park",
                    "Mateusz Maria Jurewicz",
                ],
            )
            for item in data
        ]

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
