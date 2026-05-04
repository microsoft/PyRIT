# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _MedSafetyBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the MedSafetyBench dataset.

    This dataset contains medical safety prompts across multiple categories to evaluate
    LLM safety in medical contexts.

    Reference: https://github.com/AI4LIFE-GROUP/med-safety-bench
    Paper: [@han2024medsafetybench]
    """

    def __init__(
        self,
        *,
        subset_name: Literal["train", "test", "generated", "all"] = "all",
    ) -> None:
        """
        Initialize the MedSafetyBench dataset loader.

        Args:
            subset_name: Choose from "train", "test", "generated", or "all". Defaults to "all".

        Raises:
            ValueError: If an invalid subset_name is provided.
        """
        self.subset_name = subset_name

        base_url = "https://raw.githubusercontent.com/AI4LIFE-GROUP/med-safety-bench/main/datasets"

        self.sources = []

        if subset_name == "test":
            for model in ["gpt4", "llama2"]:
                for category in range(1, 10):
                    self.sources.append(f"{base_url}/test/{model}/med_safety_demonstrations_category_{category}.csv")
        elif subset_name == "train":
            for model in ["gpt4", "llama2"]:
                for category in range(1, 10):
                    self.sources.append(f"{base_url}/train/{model}/med_safety_demonstrations_category_{category}.csv")
        elif subset_name == "generated":
            for category in range(1, 10):
                self.sources.append(f"{base_url}/med_harm_llama3/category_{category}.txt")
        elif subset_name == "all":
            for subset in ["test", "train"]:
                for model in ["gpt4", "llama2"]:
                    for category in range(1, 10):
                        self.sources.append(
                            f"{base_url}/{subset}/{model}/med_safety_demonstrations_category_{category}.csv"
                        )
            for category in range(1, 10):
                self.sources.append(f"{base_url}/med_harm_llama3/category_{category}.txt")
        else:
            raise ValueError(
                f"Invalid subset_name: {subset_name}. Expected one of: 'train', 'test', 'generated', 'all'."
            )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "medsafetybench"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch MedSafetyBench dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing MedSafetyBench prompts.

        Raises:
            KeyError: If expected keys are not found in the dataset examples.
        """
        logger.info(f"Loading MedSafetyBench dataset (subset: {self.subset_name})")

        all_prompts = []

        for source in self.sources:
            examples = self._fetch_from_url(
                source=source,
                source_type="public_url",
                cache=cache,
            )

            for ex in examples:
                prompt = ex.get("harmful_medical_request") or ex.get("prompt")
                if not prompt:
                    raise KeyError(f"No 'harmful_medical_request' or 'prompt' found in example from {source}")

                url_parts = source.split("/")
                model_type = url_parts[-2] if len(url_parts) >= 2 else "unknown"
                filename = url_parts[-1]

                category_str = ""
                category = 0

                if filename.endswith(".txt"):
                    category_str = filename.split("_")[-1].replace(".txt", "") if "_" in filename else ""
                    file_type = "generated"
                else:
                    category_str = filename.split("_")[-1].replace(".csv", "") if "_" in filename else ""
                    file_type = url_parts[-3] if len(url_parts) >= 3 else "unknown"

                if category_str.isdigit():
                    category = int(category_str)

                all_prompts.append(
                    SeedPrompt(
                        value=prompt,
                        data_type="text",
                        dataset_name=self.dataset_name,
                        harm_categories=["medical safety"],
                        description=(
                            f"Prompt from MedSafetyBench dataset - {model_type} model, "
                            f"category {category}, type {file_type}."
                        ),
                        source=source,
                    )
                )

        logger.info(f"Successfully loaded {len(all_prompts)} prompts from MedSafetyBench dataset")

        return SeedDataset(seeds=all_prompts, dataset_name=self.dataset_name)
