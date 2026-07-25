# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion


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

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 660 prompts across 6 dark-pattern categories
    tags: frozenset[str] = frozenset({"default", "safety"})

    def __init__(
        self,
        *,
        dataset_name: str = "apart/darkbench",
        config: str = "default",
    ) -> None:
        """
        Initialize the DarkBench dataset loader.

        Args:
            dataset_name: HuggingFace dataset identifier. Defaults to "apart/darkbench".
            config: Dataset configuration. Defaults to "default".
        """
        self.hf_dataset_name = dataset_name
        self.config = config

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "dark_bench"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
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
        data = await self._fetch_from_huggingface_async(
            dataset_name=self.hf_dataset_name,
            config=self.config,
            split="train",
            cache=cache,
            data_files="darkbench.tsv",
        )

        # Process into SeedPrompts
        description = (
            "DarkBench is a comprehensive benchmark designed to detect dark design patterns in large "
            "language models (LLMs). Dark patterns are manipulative techniques that influence user "
            "behavior, often against the user's best interests. The benchmark comprises 660 prompts "
            "across six categories of dark patterns, which the researchers used to evaluate 14 different "
            "models from leading AI companies including OpenAI, Anthropic, Meta, Mistral, and Google."
        )
        authors = [
            "Esben Kran",
            'Hieu Minh "Jord" Nguyen',
            "Akash Kundu",
            "Sami Jawhar",
            "Jinsuk Park",
            "Mateusz Maria Jurewicz",
        ]
        groups = ["Apart Research", "METR"]

        seed_prompts: list[SeedUnion] = []
        for item in data:
            # DarkBench's dark-pattern types (Brand bias, Sycophancy, Sneaking, ...)
            # are not a harm taxonomy, so harm categories are left empty while the
            # native pattern label is preserved in metadata.
            deceptive_pattern = item.get("Deceptive Pattern") or ""
            harm_categories: list[str] = []
            seed_prompts.append(
                SeedPrompt(
                    value=item["Example"],
                    data_type="text",
                    name="",
                    dataset_name=self.dataset_name,
                    harm_categories=harm_categories,
                    description=description,
                    source="https://huggingface.co/datasets/apart/darkbench",
                    authors=authors,
                    groups=groups,
                    metadata={
                        "deceptive_pattern": deceptive_pattern,
                    },
                )
            )

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
