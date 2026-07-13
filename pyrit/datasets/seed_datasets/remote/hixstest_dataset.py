# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from enum import Enum

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class HiXSTestLanguage(Enum):
    """
    Language to use as the primary ``value`` of each HiXSTest SeedPrompt.

    HINDI: Use the original Hindi prompt (the dataset's intended evaluation).
    ENGLISH: Use the provided English translation. Useful for sanity-checking
        the corresponding English semantics or for English-only pipelines.
    """

    HINDI = "hi"
    ENGLISH = "en"


class _HiXSTestDataset(_RemoteDatasetLoader):
    """
    Loader for the HiXSTest (Hindi Exaggerated-Safety Test) dataset from HuggingFace.

    HiXSTest is a manually-curated set of 50 exaggerated-safety prompts in Hindi (with
    English translations), companion to SGXSTest. It tests whether language models exhibit
    exaggerated-safety behavior (refusing benign prompts whose harmful interpretation is
    not warranted in Hindi cultural context).

    Each example contains:
        - prompt: the prompt text in Hindi
        - english_prompt: English translation of the prompt
        - label: "safe" or "unsafe"
        - category: the polysemous Hindi trigger word being tested (e.g. "मारना")

    By default the Hindi prompt is used as the ``SeedPrompt.value``. Pass
    ``language=HiXSTestLanguage.ENGLISH`` to use the English translation instead.
    Both the Hindi and English texts are always preserved in ``metadata`` as
    ``hindi_prompt`` and ``english_prompt``.

    Note: This is a gated dataset on HuggingFace. You must accept the terms at
    https://huggingface.co/datasets/walledai/HiXSTest before use, and provide a
    HuggingFace token (either via the ``token`` parameter or the
    ``HUGGINGFACE_TOKEN`` environment variable).

    References:
        - https://huggingface.co/datasets/walledai/HiXSTest
        - [@gupta2024walledeval]
    License: Apache-2.0
    """

    HF_DATASET_NAME: str = "walledai/HiXSTest"

    # Class-level dataset metadata for SeedDatasetMetadata discovery
    modalities: list[str] = ["text"]
    size: str = "small"  # 50 seeds
    tags: set[str] = {"default", "safety", "multilingual"}

    def __init__(
        self,
        *,
        language: HiXSTestLanguage = HiXSTestLanguage.HINDI,
        token: str | None = None,
    ) -> None:
        """
        Initialize the HiXSTest dataset loader.

        Args:
            language: Which language to use as the primary ``SeedPrompt.value``.
                Defaults to ``HiXSTestLanguage.HINDI`` (the dataset's intended language).
                Pass ``HiXSTestLanguage.ENGLISH`` to use the English translation instead.
            token: Hugging Face authentication token. If not provided, reads from the
                ``HUGGINGFACE_TOKEN`` environment variable.

        Raises:
            ValueError: If ``language`` is not a ``HiXSTestLanguage`` instance.
        """
        self._validate_enum(language, HiXSTestLanguage, "language")
        self.language = language
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "hixstest"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HiXSTest dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the HiXSTest prompts. The
                ``SeedPrompt.value`` is the Hindi prompt by default, or the English
                translation when ``language=HiXSTestLanguage.ENGLISH``. Both texts
                are always stored in ``metadata`` as ``hindi_prompt`` and
                ``english_prompt`` alongside ``label`` and ``category``.
        """
        logger.info(f"Loading HiXSTest dataset from {self.HF_DATASET_NAME} (language={self.language.value})")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split="train",
            cache=cache,
            token=self.token,
        )

        authors = [
            "Prannaya Gupta",
            "Le Qi Yau",
            "Hao Han Low",
            "I-Shiang Lee",
            "Hugo Maximus Lim",
            "Yu Xin Teoh",
            "Jia Hng Koh",
            "Dar Win Liew",
            "Rishabh Bhardwaj",
            "Rajat Bhardwaj",
            "Soujanya Poria",
        ]
        description = (
            "HiXSTest contains 50 manually-curated exaggerated-safety prompts in Hindi "
            "(with English translations), companion to SGXSTest. It tests whether language "
            "models exhibit exaggerated-safety behavior in a Hindi cultural context. "
            "Introduced in 'WalledEval: A Comprehensive Safety Evaluation Toolkit for "
            "Large Language Models' (2024)."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["Walled AI", "DeCLaRe Lab, Singapore University of Technology and Design"]

        seed_prompts: list[SeedUnion] = [
            SeedPrompt(
                value=self._select_value(item),
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[item["category"]] if item.get("category") else [],
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata={
                    "hindi_prompt": item.get("prompt", ""),
                    "english_prompt": item.get("english_prompt", ""),
                    "label": item.get("label", ""),
                    "category": item.get("category", ""),
                    "language": self.language.value,
                },
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from HiXSTest dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _select_value(self, item: dict[str, str]) -> str:
        """
        Return the prompt text to use as ``SeedPrompt.value`` based on ``self.language``.

        Args:
            item (dict): A single row from the HiXSTest dataset.

        Returns:
            str: The prompt text in the configured language.

        Raises:
            ValueError: If the selected language's prompt field is missing or empty.
        """
        key = "english_prompt" if self.language is HiXSTestLanguage.ENGLISH else "prompt"
        value = item.get(key)
        if not value:
            raise ValueError(
                f"HiXSTest row is missing required field '{key}' for language={self.language.value}: {item!r}"
            )
        return value
