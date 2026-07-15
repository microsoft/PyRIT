# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class _LibrAIDoNotAnswerDataset(_RemoteDatasetLoader):
    """
    Loader for the LibrAI 'Do Not Answer' dataset.

    This dataset contains questions across multiple risk areas and harm types
    to test LLM safety and refusal behaviors.

    Reference: [@wang2023donotanswer]
    GitHub: https://github.com/libr-ai/do-not-answer
    """

    _AUTHORS = [
        "Yuxia Wang",
        "Haonan Li",
        "Xudong Han",
        "Preslav Nakov",
        "Timothy Baldwin",
    ]

    _GROUPS = [
        "Mohamed bin Zayed University of Artificial Intelligence",
        "University of Melbourne",
    ]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 939 prompts across multiple risk areas
    tags: frozenset[str] = frozenset({"default", "safety", "refusal"})

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
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "librai_do_not_answer"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch LibrAI Do Not Answer dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the Do Not Answer prompts.
        """
        logger.info(f"Loading LibrAI Do Not Answer dataset from {self.source}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.source,
            split="train",
            cache=cache,
        )

        # Map the 12 "types_of_harm" labels (finer-grained than the 5 risk_area
        # buckets) to canonical harm categories. The native risk_area,
        # types_of_harm, and specific_harms annotations are preserved in metadata.
        harm_type_alias_overrides: dict[str, list[HarmCategory]] = {
            "Toxic language (hate speech)": [HarmCategory.HATE_SPEECH],
            "Mental Health or Overreliance Crisis": [HarmCategory.MENTAL_HEALTH, HarmCategory.EMOTIONAL],
            "Social stereotypes and unfair discrimination": [
                HarmCategory.REPRESENTATIONAL,
                HarmCategory.HATE_SPEECH,
            ],
            "Treat Chatbot as a Human": [HarmCategory.EMOTIONAL],
            "Adult Content": [HarmCategory.SEXUAL_CONTENT],
            "Compromise privacy by leaking or inferring private information (person/individual)": [HarmCategory.PPI],
            "Risks from leaking or inferring sensitive information (organization/gov)": [
                HarmCategory.PROPRIETARY_INFO,
                HarmCategory.HIGH_RISK_GOVERNMENT,
            ],
            "Nudging or advising users to perform unethical or unsafe actions": [HarmCategory.DANGEROUS_SITUATIONS],
            "Assisting illegal activities": [HarmCategory.COORDINATION_HARM],
            "Causing material harm by disseminating misinformation e.g. in medicine or law": [
                HarmCategory.INFO_INTEGRITY,
                HarmCategory.CURRENT_EVENTS_MISINFO,
            ],
            "Disseminating false or misleading information": [HarmCategory.INFO_INTEGRITY],
            "Reducing the cost of disinformation campaigns": [
                HarmCategory.INFO_INTEGRITY,
                HarmCategory.CAMPAIGNING,
            ],
        }

        seed_prompts: list[SeedUnion] = [
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
                authors=self._AUTHORS,
                groups=self._GROUPS,
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
