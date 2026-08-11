# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedObjective, SeedUnion
from pyrit.models.harm_category import HarmCategory


class _HarmBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the HarmBench dataset.

    HarmBench is a standardized evaluation framework for automated red teaming.
    It consists of a dataset of harmful behaviors across multiple categories.

    Reference: [@mazeika2024harmbench]
    """

    # Metadata
    harm_categories: list[str] = ["cybercrime", "illegal", "harmful", "chemical_biological", "harassment"]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "medium"  # 400 harmful behaviors
    tags: set[str] = {"default", "safety"}

    def __init__(
        self,
        *,
        source: str = (
            "https://raw.githubusercontent.com/centerforaisafety/HarmBench/c0423b9/data/behavior_datasets/"
            "harmbench_behaviors_text_all.csv"
        ),
        source_type: Literal["public_url", "file"] = "public_url",
    ) -> None:
        """
        Initialize the HarmBench dataset loader.

        Args:
            source: URL to the HarmBench CSV file. Defaults to the official repository.
            source_type: The type of source ('public_url' or 'file').
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "harmbench"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HarmBench dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the HarmBench examples.

        Raises:
            ValueError: If any example is missing required keys.
        """
        # Required keys to validate each example
        required_keys = {"Behavior", "SemanticCategory"}

        # Fetch the examples using the inherited method
        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        # Validate and process examples
        harm_category_alias_overrides: dict[str, list[HarmCategory]] = {
            "chemical_biological": [HarmCategory.CBRN],
            "cybercrime_intrusion": [HarmCategory.COORDINATION_HARM, HarmCategory.MALWARE],
            "cybercrime": [HarmCategory.COORDINATION_HARM, HarmCategory.MALWARE],
            "harassment_bullying": [HarmCategory.HARASSMENT],
            "illegal": [HarmCategory.COORDINATION_HARM],
            "illegal_activity": [HarmCategory.COORDINATION_HARM],
            "misinformation_disinformation": [HarmCategory.INFO_INTEGRITY],
            "harmful": [HarmCategory.OTHER],
            "copyright": [HarmCategory.COPYRIGHT],
        }

        seeds: list[SeedUnion] = []
        for example in examples:
            # Check for missing keys in the example
            missing_keys = required_keys - example.keys()
            if missing_keys:
                raise ValueError(f"Missing keys in example: {', '.join(missing_keys)}")

            # Extract data
            category = example["SemanticCategory"]

            # Standardize harm categories
            standardized_categories = self._standardize_harm_categories(
                category,
                alias_overrides=harm_category_alias_overrides,
            )

            metadata: dict[str, str | int] = {key: value for key, value in example.items() if key != "Behavior"}

            # Create SeedPrompt
            seed_prompt = SeedObjective(
                value=example["Behavior"],
                name="HarmBench Examples",
                dataset_name=self.dataset_name,
                harm_categories=standardized_categories,
                description=(
                    "A dataset of HarmBench examples containing various categories such as chemical, "
                    "biological, illegal activities, etc."
                ),
                source="https://github.com/centerforaisafety/HarmBench",
                authors=[
                    "Mantas Mazeika",
                    "Long Phan",
                    "Xuwang Yin",
                    "Andy Zou",
                    "Zifan Wang",
                    "Norman Mu",
                    "Elham Sakhaee",
                    "Nathaniel Li",
                    "Steven Basart",
                    "Bo Li",
                    "David Forsyth",
                    "Dan Hendrycks",
                ],
                metadata=metadata,
                groups=[
                    "University of Illinois Urbana-Champaign",
                    "Center for AI Safety",
                    "Carnegie Mellon University",
                    "University of California, Berkeley",
                    "Microsoft",
                ],
            )
            seeds.append(seed_prompt)

        # Create and return SeedDataset
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
