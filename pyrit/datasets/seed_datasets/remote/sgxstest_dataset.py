# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
import warnings
from enum import Enum

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedObjective

logger = logging.getLogger(__name__)


class SGXSTestLabel(Enum):
    """
    Filter for which subset of SGXSTest prompts to load.

    UNSAFE: Only the 100 unsafe (truly-harmful) prompts.
    SAFE:   Only the 100 safe prompts (the over-refusal targets).
    ALL:    Both, all 200 prompts.
    """

    UNSAFE = "unsafe"
    SAFE = "safe"
    ALL = "all"


class _SGXSTestDataset(_RemoteDatasetLoader):
    """
    Loader for the SGXSTest (Singapore exaggerated-safety test) dataset from HuggingFace.

    SGXSTest contains 100 manually-curated safe/unsafe prompt pairs (200 prompts total) that
    test exaggerated-safety behavior of LLMs in a Singaporean cultural context. It adapts
    the 10 hazard categories of XSTest (Röttger et al. 2023): homonym, figurative language,
    safe targets, safe contexts, definitions, real discrimination / nonsense group, nonsense
    discrimination / real group, historical events, privacy (public), and privacy (fiction).

    By default only the unsafe half is loaded, matching the typical red-teaming use case.
    Pass ``label=SGXSTestLabel.SAFE`` or ``label=SGXSTestLabel.ALL`` to change this.

    References:
        - https://huggingface.co/datasets/walledai/SGXSTest
        - [@gupta2024walledeval]
    License: Apache-2.0

    Note: This is a gated dataset on HuggingFace. You must accept the terms at
    https://huggingface.co/datasets/walledai/SGXSTest before use, and provide a
    HuggingFace token (either via the ``token`` constructor argument or by setting
    the ``HUGGINGFACE_TOKEN`` environment variable).
    """

    HF_DATASET_NAME: str = "walledai/SGXSTest"

    # Metadata
    harm_categories: list[str] = [
        "definitions",
        "figurative language",
        "historical events",
        "homonym",
        "nonsense discrimination, real group",
        "privacy (fiction)",
        "privacy (public)",
        "real discrimination, nonsense group",
        "safe contexts",
        "safe targets",
    ]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "small"  # 100 prompts
    tags: set[str] = {"safety", "multilingual"}

    def __init__(
        self,
        *,
        label: SGXSTestLabel = SGXSTestLabel.UNSAFE,
        split: str | None = None,
        token: str | None = None,
    ) -> None:
        """
        Initialize the SGXSTest dataset loader.

        Args:
            label: Which subset of prompts to load. Defaults to ``SGXSTestLabel.UNSAFE``
                (the truly-harmful prompts). Use ``SGXSTestLabel.SAFE`` for the
                over-refusal targets or ``SGXSTestLabel.ALL`` for the full 200-prompt set.
            split: **Deprecated.** Upstream ``walledai/SGXSTest`` publishes only the
                ``"train"`` split, so this kwarg has no effect. It will be removed in
                v0.16.0.
            token: Hugging Face authentication token. If not provided, reads from
                the HUGGINGFACE_TOKEN env var.

        Raises:
            ValueError: If ``label`` is not an SGXSTestLabel member.
        """
        if split is not None:
            warnings.warn(
                "'split' is deprecated and will be removed in v0.16.0. "
                "Upstream walledai/SGXSTest publishes only the 'train' split, "
                "so this kwarg has no effect.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._validate_enum(value=label, enum_cls=SGXSTestLabel, label="label")

        self.label = label
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "sgxstest"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch SGXSTest dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SGXSTest objectives filtered by
            ``self.label``. Each SeedObjective's ``metadata`` dict contains ``label``
            ("safe" or "unsafe") and ``category`` (one of the 10 hazard categories).

        Raises:
            ValueError: If the dataset is empty after filtering.
        """
        logger.info(f"Loading SGXSTest dataset from {self.HF_DATASET_NAME} (label={self.label.value})")

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
            "SGXSTest contains 100 manually-curated safe/unsafe prompt pairs (200 prompts total) "
            "testing exaggerated-safety behavior of LLMs in a Singaporean cultural context. Adapts "
            "the 10 hazard categories of XSTest (Röttger et al. 2023). Introduced in 'WalledEval: A "
            "Comprehensive Safety Evaluation Toolkit for Large Language Models' (Gupta et al. 2024)."
        )
        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["Walled AI", "DeCLaRe Lab, Singapore University of Technology and Design"]

        seed_objectives = [
            SeedObjective(
                value=item["prompt"],
                dataset_name=self.dataset_name,
                harm_categories=[item["category"]] if item.get("category") else [],
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                metadata={
                    "label": item["label"],
                    "category": item["category"],
                },
            )
            for item in data
            if self.label == SGXSTestLabel.ALL or item.get("label") == self.label.value
        ]

        if not seed_objectives:
            raise ValueError(
                f"SeedDataset is empty after filtering by label={self.label.value!r}. "
                f"Expected one of: 'safe', 'unsafe'."
            )

        logger.info(f"Successfully loaded {len(seed_objectives)} objectives from SGXSTest dataset")

        return SeedDataset(seeds=seed_objectives, dataset_name=self.dataset_name)
