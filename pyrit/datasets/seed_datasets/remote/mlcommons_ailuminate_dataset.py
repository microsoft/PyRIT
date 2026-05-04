# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _MLCommonsAILuminateDataset(_RemoteDatasetLoader):
    """
    Loader for the AILuminate v1.0 DEMO Prompt Set.

    This dataset contains 1,200 human-generated prompts covering 12 different
    hazard categories. It's a 10% subset of the full AILuminate training dataset.

    Reference: https://github.com/mlcommons/ailuminate
    Paper: [@vidgen2024ailuminate]
    """

    HAZARD_CATEGORIES_MAP = {
        "vcr": "Violent Crimes",
        "src": "Sex-Related Crimes",
        "cse": "Child Sexual Exploitation",
        "ssh": "Suicide & Self-Harm",
        "iwp": "Indiscriminate Weapons (CBRNE)",
        "ipv": "Intellectual Property Violations",
        "dfm": "Defamation",
        "ncr": "Non-Violent Crimes",
        "hte": "Hate",
        "prv": "Privacy Violations",
        "spc_ele": "Specialized Advice; elections",
        "spc_fin": "Specialized Advice; financial",
        "spc_hlt": "Specialized Advice; health",
        "sxc_prn": "Sexual Content; pornographic",
        "spc_lgl": "Specialized Advice; legal",
    }

    def __init__(
        self,
        *,
        source: str = (
            "https://raw.githubusercontent.com/mlcommons/ailuminate/refs/heads/main/"
            "airr_official_1.0_demo_en_us_prompt_set_release.csv"
        ),
        source_type: Literal["public_url", "file"] = "public_url",
    ) -> None:
        """
        Initialize the AILuminate dataset loader.

        Args:
            source: URL to the AILuminate CSV file. Defaults to official repository.
            source_type: The type of source ('public_url' or 'file').
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "mlcommons_ailuminate"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch AILuminate dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the AILuminate prompts.
        """
        logger.info(f"Loading MLCommons AILuminate dataset from {self.source}")

        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        seed_prompts = [
            SeedPrompt(
                value=example["prompt_text"],
                data_type="text",
                dataset_name=self.dataset_name,
                # Saving both the full hazard category name and the abbreviation
                harm_categories=[self.HAZARD_CATEGORIES_MAP[example["hazard"]], example["hazard"]],
                description=(
                    "This dataset contains the DEMO prompt library of the AILuminate 1.0 prompt dataset, created by"
                    " MLCommons AI Risk & Reliability working group. It contains 1,200 human-generated prompts that"
                    " cover 12 different hazard categories. The dataset is a mix of adversarial and non-adversarial"
                    " prompts. This dataset is a 10% subset of the full AILuminate training dataset."
                ),
                source="https://github.com/mlcommons/ailuminate",
            )
            for example in examples
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from MLCommons AILuminate dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
