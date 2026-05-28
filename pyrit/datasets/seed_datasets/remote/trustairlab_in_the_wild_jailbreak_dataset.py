# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Any

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)

_HF_REPO_ID = "TrustAIRLab/in-the-wild-jailbreak-prompts"
_CONFIG = "jailbreak_2023_12_25"

_AUTHORS: list[str] = [
    "Xinyue Shen",
    "Zeyuan Chen",
    "Michael Backes",
    "Yun Shen",
    "Yang Zhang",
]

_GROUPS: list[str] = ["CISPA Helmholtz Center for Information Security"]


class TrustAIRLabPlatform(Enum):
    """Source platforms for the TrustAIRLab in-the-wild jailbreak prompts (jailbreak_2023_12_25 config)."""

    DISCORD = "discord"
    REDDIT = "reddit"
    WEBSITE = "website"


class _TrustAIRLabInTheWildJailbreakDataset(_RemoteDatasetLoader):
    """
    Loader for the TrustAIRLab in-the-wild jailbreak prompts dataset.

    This loader returns the ``jailbreak_2023_12_25`` config, which is the cumulative
    snapshot covering December 2022 through December 2023 — i.e. it is a strict
    superset of the earlier ``jailbreak_2023_05_07`` snapshot (1,405 rows total per
    the paper). Each row is a literal jailbreak prompt collected from Discord,
    Reddit, public websites, or open-source datasets.

    Warning: the corpus contains user-posted jailbreak prompts that include adult,
    hateful, and illegal-themed content (the paper explicitly flags this). The
    prompts are not scrubbed.

    License: MIT.
    Reference: [@shen2023donotanything]
    HuggingFace: https://huggingface.co/datasets/TrustAIRLab/in-the-wild-jailbreak-prompts
    Paper: https://arxiv.org/abs/2308.03825
    """

    HF_DATASET_NAME: str = _HF_REPO_ID
    modalities: list[str] = ["text"]
    size: str = "large"
    tags: set[str] = {"safety", "jailbreak"}

    def __init__(
        self,
        *,
        platforms: list[TrustAIRLabPlatform] | None = None,
        deduplicate: bool = False,
    ) -> None:
        """
        Initialize the TrustAIRLab in-the-wild jailbreak dataset loader.

        Args:
            platforms (list[TrustAIRLabPlatform] | None): Source platforms to include.
                Defaults to all four (``DISCORD``, ``REDDIT``, ``WEBSITE``, ``DATASET``).
            deduplicate (bool): When True, drop exact-text duplicate prompts. The
                upstream README explicitly notes duplicates in the ``prompt`` field;
                default is False (lossless) to preserve the original corpus shape.

        Raises:
            ValueError: If ``platforms`` is an empty list or contains non-enum values.
        """
        self.platforms = self._resolve_platforms(platforms)
        self.deduplicate = deduplicate
        self.source = f"https://huggingface.co/datasets/{_HF_REPO_ID}"

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "trustairlab_in_the_wild_jailbreak"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the in-the-wild jailbreak prompts and return as a SeedDataset.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered jailbreak prompts.

        Raises:
            ValueError: If the filter combination produces zero seeds.
        """
        logger.info(
            f"Loading TrustAIRLab in-the-wild jailbreak dataset "
            f"(platforms={[p.value for p in self.platforms]}, deduplicate={self.deduplicate})"
        )

        rows = await self._fetch_from_huggingface(
            dataset_name=_HF_REPO_ID,
            config=_CONFIG,
            split="train",
            cache=cache,
        )

        seed_prompts = self._rows_to_seeds(rows=rows)

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from TrustAIRLab in-the-wild jailbreak dataset")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _rows_to_seeds(self, *, rows: Any) -> list[SeedPrompt]:
        """
        Convert raw HuggingFace rows into filtered SeedPrompts.

        Args:
            rows (Any): The HuggingFace dataset object.

        Returns:
            list[SeedPrompt]: SeedPrompts that survived the configured filters.
        """
        kept_platforms = {p.value for p in self.platforms}
        seen_prompts: set[str] = set()

        seeds: list[SeedPrompt] = []
        for row in rows:
            prompt_text = row.get("prompt")
            if not prompt_text:
                continue

            platform = row.get("platform")
            if platform not in kept_platforms:
                continue

            if self.deduplicate:
                if prompt_text in seen_prompts:
                    continue
                seen_prompts.add(prompt_text)

            source_community = row.get("community") or row.get("source") or ""
            seeds.append(
                SeedPrompt(
                    value=prompt_text,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=[],
                    source=self.source,
                    authors=_AUTHORS,
                    groups=_GROUPS,
                    metadata={
                        "platform": platform,
                        "source_community": source_community,
                        "created_at": row.get("created_at"),
                        "snapshot_date": row.get("date"),
                    },
                )
            )
        return seeds

    @staticmethod
    def _resolve_platforms(
        platforms: list[TrustAIRLabPlatform] | None,
    ) -> list[TrustAIRLabPlatform]:
        """
        Validate and normalize the requested list of platforms.

        Args:
            platforms (list[TrustAIRLabPlatform] | None): User-supplied list, or None.

        Returns:
            list[TrustAIRLabPlatform]: Normalized list (defaults to all four platforms).

        Raises:
            ValueError: If ``platforms`` is an empty list or contains non-enum values.
        """
        if platforms is None:
            return list(TrustAIRLabPlatform)
        if not platforms:
            raise ValueError(
                "TrustAIRLab platforms must not be empty. Pass None to load all four, "
                "or supply at least one TrustAIRLabPlatform value."
            )
        _RemoteDatasetLoader._validate_enums(platforms, TrustAIRLabPlatform, "platform")
        return list(platforms)
