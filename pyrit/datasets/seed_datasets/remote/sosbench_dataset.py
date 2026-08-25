# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _SOSBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the SOSBench dataset.

    SOSBench is a regulation-grounded, hazard-focused benchmark encompassing six high-risk
    scientific domains: chemistry, biology, medicine, pharmacology, physics, and psychology.
    The benchmark comprises 3,000 prompts derived from real-world regulations and laws.

    Reference: [@jiang2025sosbench]
    """

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 3,000 hazard-focused scientific prompts across 6 domains
    tags: frozenset[str] = frozenset({"safety", "medical", "cybersecurity"})

    def __init__(
        self,
        *,
        source: str = "SOSBench/SOSBench",
    ) -> None:
        """
        Initialize the SOSBench dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "SOSBench/SOSBench".
        """
        self.source = source

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "sosbench"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch SOSBench dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SOSBench prompts.
        """
        logger.info(f"Loading SOSBench dataset from {self.source}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.source,
            config="default",
            split="train",
            cache=cache,
        )

        # Map SOSBench's six scientific domains to PyRIT harm categories. Keys must
        # match the dataset's actual ``subject`` values ("medical"/"pharmacy", not
        # "medicine"/"pharmacology"). The mapping reflects the content each domain
        # actually contains: biology/medical prompts are infectious-disease and
        # health misinformation (PUBLIC_HEALTH), physics prompts concern radioactive
        # isotopes (CBRN, radiological), pharmacy prompts synthesize designer drugs
        # (PSEUDO_PHARMA), and chemistry prompts describe hazardous synthesis (CBRN).
        alias_overrides: dict[str, list[HarmCategory]] = {
            "chemistry": [HarmCategory.CBRN],
            "biology": [HarmCategory.PUBLIC_HEALTH],
            "physics": [HarmCategory.CBRN],
            "medical": [HarmCategory.PUBLIC_HEALTH],
            "pharmacy": [HarmCategory.PSEUDO_PHARMA],
            "psychology": [HarmCategory.MENTAL_HEALTH],
        }

        seed_prompts: list[SeedUnion] = [
            SeedPrompt(
                value=item["goal"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=self._standardize_harm_categories(
                    item.get("subject"),
                    alias_overrides=alias_overrides,
                ),
                metadata={"sosbench_subject": item.get("subject")},
                description=(
                    "SOSBench is a regulation-grounded, hazard-focused benchmark encompassing "
                    "six high-risk scientific domains: chemistry, biology, medicine, pharmacology, "
                    "physics, and psychology. The benchmark comprises 3,000 prompts derived from "
                    "real-world regulations and laws, systematically expanded via an LLM-assisted "
                    "evolutionary pipeline that introduces diverse, realistic misuse scenarios"
                    " (e.g., detailed explosive synthesis instructions involving advanced"
                    " chemical formulas)."
                ),
                source=f"https://huggingface.co/datasets/{self.source}",
                authors=[
                    "Fengqing Jiang",
                    "Fengbo Ma",
                    "Zhangchen Xu",
                    "Yuetai Li",
                    "Zixin Rao",
                    "Bhaskar Ramasubramanian",
                    "Luyao Niu",
                    "Bo Li",
                    "Xianyan Chen",
                    "Zhen Xiang",
                    "Radha Poovendran",
                ],
                groups=[
                    "University of Washington",
                    "University of Georgia",
                    "Western Washington University",
                    "University of Illinois Urbana-Champaign",
                ],
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from SOSBench dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
