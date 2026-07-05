# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _LibrAIDoNotAnswerDataset(_RemoteDatasetLoader):
    """
    Loader for the LibrAI 'Do Not Answer' dataset.

    This dataset contains questions across multiple risk areas and harm types
    to test LLM safety and refusal behaviors.

    Reference: [@wang2023donotanswer]
    GitHub: https://github.com/libr-ai/do-not-answer
    """

    def __init__(
        self,
        *,
        source: str = "LibrAI/do-not-answer",
    ) -> None:
        """
        Initialize the LibrAI Do Not Answer dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "LibrAI/do-not-answer".
        """
        self.source = source

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "librai_do_not_answer"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch LibrAI Do Not Answer dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the Do Not Answer prompts.
        """
        logger.info(f"Loading LibrAI Do Not Answer dataset from {self.source}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            split="train",
            cache=cache,
        )

        # Map the 12 "types_of_harm" labels (finer-grained than the 5 risk_area
        # buckets) to canonical harm categories. The native risk_area,
        # types_of_harm, and specific_harms annotations are preserved in metadata.
        harm_type_alias_overrides: dict[str, list[str]] = {
            "Toxic language (hate speech)": ["HATESPEECH"],
            "Mental Health or Overreliance Crisis": ["MENTAL_HEALTH", "EMOTIONAL"],
            "Social stereotypes and unfair discrimination": ["REPRESENTATIONAL", "HATESPEECH"],
            "Treat Chatbot as a Human": ["EMOTIONAL"],
            "Adult Content": ["SEXUAL_CONTENT"],
            "Compromise privacy by leaking or inferring private information (person/individual)": ["PPI"],
            "Risks from leaking or inferring sensitive information (organization/gov)": [
                "PROPRIETARY_INFO",
                "HIGH_RISK_GOVERNMENT",
            ],
            "Nudging or advising users to perform unethical or unsafe actions": ["DANGEROUS_SITUATIONS"],
            "Assisting illegal activities": ["COORDINATION_HARM"],
            "Causing material harm by disseminating misinformation e.g. in medicine or law": [
                "INFO_INTEGRITY",
                "CURRENT_EVENTS_MISINFO",
            ],
            "Disseminating false or misleading information": ["INFO_INTEGRITY"],
            "Reducing the cost of disinformation campaigns": ["INFO_INTEGRITY", "CAMPAIGNING"],
        }

        seed_prompts = [
            SeedPrompt(
                value=entry["question"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=self._standardize_harm_categories(
                    entry.get("types_of_harm"),
                    alias_overrides=harm_type_alias_overrides,
                ),
                description=(
                    f"This is a prompt from the 'Do Not Answer' dataset under the risk area: {entry['risk_area']}, "
                    f"harm type: {entry['types_of_harm']}, and specific harm: {entry['specific_harms']}."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
                metadata={
                    "risk_area": entry.get("risk_area", ""),
                    "types_of_harm": entry.get("types_of_harm", ""),
                    "specific_harms": entry.get("specific_harms", ""),
                },
            )
            for entry in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from LibrAI Do Not Answer dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
