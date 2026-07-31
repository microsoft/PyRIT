# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import TYPE_CHECKING, Literal

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class _MLCommonsAILuminateDataset(_RemoteDatasetLoader):
    """
    Loader for the AILuminate v1.0 DEMO Prompt Set.

    This dataset contains 1,200 human-generated prompts covering 12 different
    hazard categories. It's a 10% subset of the full AILuminate training dataset.

    Reference: https://github.com/mlcommons/ailuminate
    Paper: [@ghosh2025ailuminate]
    """

    _AUTHORS = [
        "Shaona Ghosh",
        "Heather Frase",
        "Adina Williams",
        "Sarah Luger",
        "Paul Röttger",
        "and others",
    ]

    _GROUPS = ["MLCommons AI Safety Working Group"]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 1,200 hazard-category prompts
    tags: frozenset[str] = frozenset({"default", "safety"})

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
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "mlcommons_ailuminate"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
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
        harm_category_alias_overrides: dict[str, list[HarmCategory]] = {
            "vcr": [HarmCategory.VIOLENT_CONTENT],
            "violent crimes": [HarmCategory.VIOLENT_CONTENT],
            "src": [HarmCategory.SEXUAL_CONTENT],
            "sex-related crimes": [HarmCategory.SEXUAL_CONTENT],
            "cse": [HarmCategory.SEXUAL_CONTENT, HarmCategory.GROOMING],
            "child sexual exploitation": [HarmCategory.SEXUAL_CONTENT, HarmCategory.GROOMING],
            "ssh": [HarmCategory.SUICIDE, HarmCategory.SELF_HARM],
            "suicide & self-harm": [HarmCategory.SUICIDE, HarmCategory.SELF_HARM],
            "iwp": [HarmCategory.CBRN, HarmCategory.MILITARY],
            "indiscriminate weapons (cbrne)": [HarmCategory.CBRN, HarmCategory.MILITARY],
            "ipv": [HarmCategory.COPYRIGHT, HarmCategory.TRADEMARK, HarmCategory.PLAGIARISM],
            "intellectual property violations": [
                HarmCategory.COPYRIGHT,
                HarmCategory.TRADEMARK,
                HarmCategory.PLAGIARISM,
            ],
            "dfm": [HarmCategory.REPUTATIONAL_DAMAGE],
            "defamation": [HarmCategory.REPUTATIONAL_DAMAGE],
            "ncr": [HarmCategory.COORDINATION_HARM],
            "non-violent crimes": [HarmCategory.COORDINATION_HARM],
            "hte": [HarmCategory.HATE_SPEECH, HarmCategory.HARASSMENT],
            "hate": [HarmCategory.HATE_SPEECH, HarmCategory.HARASSMENT],
            "prv": [HarmCategory.PPI],
            "privacy violations": [HarmCategory.PPI],
            "spc_ele": [HarmCategory.CAMPAIGNING],
            "specialized advice; elections": [HarmCategory.CAMPAIGNING],
            "spc_fin": [HarmCategory.FINANCIAL_ADVICE],
            "specialized advice; financial": [HarmCategory.FINANCIAL_ADVICE],
            "spc_hlt": [HarmCategory.PUBLIC_HEALTH],
            "specialized advice; health": [HarmCategory.PUBLIC_HEALTH],
            "sxc_prn": [HarmCategory.SEXUAL_CONTENT],
            "sexual content; pornographic": [HarmCategory.SEXUAL_CONTENT],
            "spc_lgl": [HarmCategory.LEGAL_ADVICE],
            "specialized advice; legal": [HarmCategory.LEGAL_ADVICE],
        }

        seed_prompts: list[SeedUnion] = [
            SeedPrompt(
                value=example["prompt_text"],
                data_type="text",
                dataset_name=self.dataset_name,
                # Standardize using both the full hazard category name and the abbreviation,
                # and preserve the original AILuminate hazard labels in metadata.
                harm_categories=self._standardize_harm_categories(
                    [self.HAZARD_CATEGORIES_MAP[example["hazard"]], example["hazard"]],
                    alias_overrides=harm_category_alias_overrides,
                ),
                metadata={
                    "mlcommons_ailuminate_hazard": example["hazard"],
                    "mlcommons_ailuminate_hazard_name": self.HAZARD_CATEGORIES_MAP[example["hazard"]],
                },
                description=(
                    "This dataset contains the DEMO prompt library of the AILuminate 1.0 prompt dataset, created by"
                    " MLCommons AI Risk & Reliability working group. It contains 1,200 human-generated prompts that"
                    " cover 12 different hazard categories. The dataset is a mix of adversarial and non-adversarial"
                    " prompts. This dataset is a 10% subset of the full AILuminate training dataset."
                ),
                source="https://github.com/mlcommons/ailuminate",
                authors=self._AUTHORS,
                groups=self._GROUPS,
            )
            for example in examples
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from MLCommons AILuminate dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
