# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)

_AUTHORS: list[str] = [
    "Yi Zeng",
    "Yu Yang",
    "Andy Zhou",
    "Jeffrey Ziwei Tan",
    "Yuheng Tu",
    "Yifan Mai",
    "Kevin Klyman",
    "Minzhou Pan",
    "Ruoxi Jia",
    "Dawn Song",
    "Percy Liang",
    "Bo Li",
]
_DATASET_URL = "https://huggingface.co/datasets/stanford-crfm/air-bench-2024"
_DESCRIPTION = (
    "AIR-Bench 2024 is a safety benchmark whose risk categories are taken from government "
    "regulations and company usage policies, organized as a four-level taxonomy."
)
_REQUIRED_COLUMNS: tuple[str, ...] = ("cate-idx", "l2-name", "l3-name", "l4-name", "prompt")


class AIRBenchRegion(Enum):
    """
    AIR-Bench 2024 subsets, one per HuggingFace config.

    ``DEFAULT`` is the full benchmark. The regional subsets select the risk categories that
    the named jurisdiction's rules cover.
    """

    DEFAULT = "default"
    US = "us"
    EU_MANDATORY = "eu_mandatory"
    EU_COMPREHENSIVE = "eu_comprehensive"
    CHINA = "china"


class _AIRBenchDataset(_RemoteDatasetLoader):
    """
    Loader for AIR-Bench 2024, a safety benchmark built on a risk taxonomy from regulations and policies.

    The full benchmark has 5,694 malicious prompts under a four-level taxonomy with 314
    fine-grained risk categories. ``region`` selects the HuggingFace config: the full
    benchmark (``DEFAULT``) or a subset for the US, the EU (mandatory or comprehensive) or
    China. Every row becomes one ``SeedPrompt`` with the level-2 category (``l2-name``) as its
    harm category, and the category id (``cate-idx``) and level-3 and level-4 names in
    ``metadata``. Many rows rewrite the same request on purpose (dialect, an appeal to
    authority, and so on), so rows are not deduplicated. The ``judge_prompts`` config is not
    loaded.

    Reference: [@zeng2024airbench]

    HuggingFace: https://huggingface.co/datasets/stanford-crfm/air-bench-2024 (CC-BY-4.0),
    pinned to a known revision.

    Warning: These prompts are written to elicit harmful responses and may contain offensive
    content.
    """

    HF_DATASET_NAME: str = "stanford-crfm/air-bench-2024"
    HF_REVISION: str = "976d9fbb0f52bce704dcbef5bd7a2652e65f2a21"
    HF_SPLIT: str = "test"

    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    harm_categories: list[str] = [
        "child harm",
        "criminal activities",
        "deception",
        "defamation",
        "discrimination/bias",
        "economic harm",
        "fundamental rights",
        "hate/toxicity",
        "manipulation",
        "operational misuses",
        "political usage",
        "privacy",
        "security risks",
        "self-harm",
        "sexual content",
        "violence & extremism",
    ]
    size: str = "huge"  # 5694 prompts (default config)
    tags: frozenset[str] = frozenset({"default", "safety"})

    def __init__(
        self,
        *,
        region: AIRBenchRegion = AIRBenchRegion.DEFAULT,
    ) -> None:
        """
        Initialize the AIR-Bench 2024 dataset loader.

        Args:
            region: The benchmark subset to load. Defaults to the full benchmark.

        Raises:
            ValueError: If ``region`` is not an ``AIRBenchRegion``.
        """
        self._validate_enum(region, AIRBenchRegion, "region")
        self._region = region

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "air_bench_2024"

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the selected AIR-Bench 2024 subset from HuggingFace and return it as a SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: One prompt per AIR-Bench row.

        Raises:
            ValueError: If a row is missing a field or the dataset is empty.
        """
        rows = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            config=self._region.value,
            split=self.HF_SPLIT,
            cache=cache,
            revision=self.HF_REVISION,
        )

        seed_prompts: list[SeedPrompt] = []
        for index, row in enumerate(rows):
            missing = [
                column for column in _REQUIRED_COLUMNS if not isinstance(row.get(column), str) or not row[column]
            ]
            if missing:
                raise ValueError(f"AIR-Bench row {index} is missing {', '.join(missing)}.")
            seed_prompts.append(
                SeedPrompt(
                    value=row["prompt"],
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=[row["l2-name"]],
                    description=_DESCRIPTION,
                    authors=_AUTHORS,
                    source=_DATASET_URL,
                    metadata={
                        "region": self._region.value,
                        "cate_idx": row["cate-idx"],
                        "l3_name": row["l3-name"],
                        "l4_name": row["l4-name"],
                    },
                )
            )

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info("Loaded %s prompts from AIR-Bench 2024 (%s)", len(seed_prompts), self._region.value)
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
