# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Literal

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote._image_cache import (
    fetch_and_cache_image_async,
)
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class SIUOCategory(Enum):
    """Categories in the SIUO dataset, using the exact strings from the source JSON."""

    SELF_HARM = "self-harm"
    ILLEGAL_ACTIVITIES_CRIME = "illegal activities & crime"
    PRIVACY_VIOLATION = "privacy violation"
    MORALITY = "morality"
    DANGEROUS_BEHAVIOR = "dangerous behavior"
    DISCRIMINATION_STEREOTYPING = "discrimination & stereotyping"
    INFORMATION_MISINTERPRETATION = "information misinterpretation"
    RELIGION_BELIEFS = "religion beliefs"
    CONTROVERSIAL_TOPICS_POLITICS = "controversial topics, politics"


class _SIUODataset(_RemoteDatasetLoader):
    """
    Loader for the SIUO (Safe Inputs but Unsafe Output) multimodal benchmark.

    SIUO probes cross-modality safety alignment in vision-language models. Each
    example consists of an image and a text question that are individually safe,
    but whose combination implies an unsafe scenario across 9 critical safety
    domains (self-harm, illegal activities & crime, privacy violation, morality,
    dangerous behavior, discrimination & stereotyping, information
    misinterpretation, religion beliefs, controversial topics & politics).

    Each example is returned as a 3-piece group sharing a prompt_group_id:
    a SeedObjective carrying the text question, plus a text SeedPrompt and an
    image SeedPrompt (both at sequence=0) that together form a single multimodal
    user message delivered to the target. The dataset's safety_warning field is
    preserved in each prompt's metadata so downstream scorers can use it as the
    gold rationale.

    Images are fetched from the HuggingFace mirror (sinwang/SIUO), which bundles
    them under images/. The GitHub repo points users at a Google Drive ZIP, but
    the HF mirror avoids that and lets us fetch via plain HTTPS.

    Note: The first call may be slow as images are downloaded from the
    HuggingFace dataset. Subsequent calls reuse the local image cache.

    Reference: [@wang2025siuo]
    Paper: https://arxiv.org/abs/2406.15279
    """

    _DESCRIPTION: ClassVar[str] = (
        "A multimodal example from the SIUO (Safe Inputs but Unsafe Output) benchmark. "
        "Each example pairs an individually-safe image with an individually-safe text "
        "question; the harm only emerges when the two are combined."
    )

    _AUTHORS: ClassVar[list[str]] = [
        "Siyin Wang",
        "Xingsong Ye",
        "Qinyuan Cheng",
        "Junwen Duan",
        "Shimin Li",
        "Jinlan Fu",
        "Xipeng Qiu",
        "Xuanjing Huang",
    ]

    _GROUPS: ClassVar[list[str]] = [
        "Fudan University",
        "National University of Singapore",
        "Shanghai AI Laboratory",
    ]

    HF_COMMIT_SHA: str = "024e80a01795376b9fed12f8073a12f2275f22ee"
    GEN_JSON_URL: str = f"https://huggingface.co/datasets/sinwang/SIUO/resolve/{HF_COMMIT_SHA}/siuo_gen.json"
    IMAGE_BASE_URL: str = f"https://huggingface.co/datasets/sinwang/SIUO/resolve/{HF_COMMIT_SHA}/images/"
    PAPER_URL: str = "https://arxiv.org/abs/2406.15279"

    HF_DATASET_NAME: str = "sinwang/SIUO"
    harm_categories: tuple[str, ...] = (
        "self-harm",
        "illegal",
        "privacy",
        "morality",
        "dangerous behavior",
        "discrimination",
        "misinformation",
        "religion",
        "controversial topics",
    )
    modalities: tuple[str, ...] = ("image", "text")
    size: str = "medium"
    tags: frozenset[str] = frozenset({"default", "safety", "multimodal"})

    def __init__(
        self,
        *,
        source: str = GEN_JSON_URL,
        source_type: Literal["public_url", "file"] = "public_url",
        categories: list[SIUOCategory] | None = None,
    ) -> None:
        """
        Initialize the SIUO dataset loader.

        Args:
            source (str): URL or file path to siuo_gen.json. Defaults to the
                HuggingFace mirror pinned to a commit SHA for reproducibility.
            source_type (Literal["public_url", "file"]): Whether source is a
                public URL or a local file path. Defaults to 'public_url'.
            categories (list[SIUOCategory] | None): Optional filter; only rows
                whose category matches one of these enum values are included.
                If None, every category is included.

        Raises:
            ValueError: If categories contains a non-SIUOCategory value.
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        self.categories = categories

        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(categories, SIUOCategory, "SIUO category")

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "siuo"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the SIUO dataset and return it as a SeedDataset.

        For each kept row, produces three seeds that share a prompt_group_id:
        a SeedObjective whose value is the row's question, a text SeedPrompt
        carrying the same question at sequence=0, and an image SeedPrompt at
        sequence=0 whose value is the local path of the cached image. The image
        and text prompts together form a single multimodal user message.

        Args:
            cache (bool): Whether to cache the fetched JSON and image files.
                Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the multimodal seed groups.

        Raises:
            ValueError: If a row is missing a required key, or if no row passes
                the configured filters.
        """
        logger.info(f"Loading SIUO dataset from {self.source}")

        required_keys = {"question_id", "image", "question", "category", "safety_warning"}
        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        seeds: list[SeedUnion] = []
        failed_image_count = 0
        kept_categories = {cat.value for cat in self.categories} if self.categories is not None else None

        for example in examples:
            missing_keys = required_keys - example.keys()
            if missing_keys:
                raise ValueError(f"Missing keys in example: {', '.join(sorted(missing_keys))}")

            category = example["category"]
            if kept_categories is not None and category not in kept_categories:
                continue

            try:
                group = await self._build_seed_group_async(example=example)
            except Exception as e:
                failed_image_count += 1
                logger.warning(
                    f"[SIUO] Failed to fetch image for question_id "
                    f"{example.get('question_id')}: {e}. Skipping this example."
                )
                continue

            seeds.extend(group)

        if failed_image_count > 0:
            logger.warning(f"[SIUO] Skipped {failed_image_count} example(s) due to image fetch failures")

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seeds)} seeds from SIUO dataset")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    async def _build_seed_group_async(self, *, example: dict[str, str]) -> list["SeedUnion"]:
        """
        Build a 3-piece (objective + text + image) seed group for a single SIUO row.

        Args:
            example (dict[str, str]): A single example row from siuo_gen.json.

        Returns:
            list[Seed]: [SeedObjective, text SeedPrompt, image SeedPrompt],
                all sharing a freshly generated prompt_group_id. The text and
                image prompts both use sequence=0 so they are delivered to the
                target as a single multimodal user message.

        Raises:
            Exception: Propagates any failure raised while fetching the image
                so the caller can skip the row.
        """
        question_id = example["question_id"]
        question = example["question"]
        category = example["category"]
        image_filename = example["image"]
        safety_warning = example["safety_warning"]

        image_url = f"{self.IMAGE_BASE_URL}{image_filename}"
        image_stem = image_filename.rsplit(".", 1)[0]
        local_image_path = await self._fetch_and_save_image_async(
            image_url=image_url,
            image_stem=image_stem,
        )

        group_id = uuid.uuid4()
        metadata: dict[str, str | int] = {
            "question_id": question_id,
            "category": category,
            "safety_warning": safety_warning,
        }

        objective = SeedObjective(
            value=question,
            name=f"SIUO Objective - {question_id}",
            dataset_name=self.dataset_name,
            harm_categories=[category],
            description=self._DESCRIPTION,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.PAPER_URL,
            prompt_group_id=group_id,
        )

        text_prompt = SeedPrompt(
            value=question,
            data_type="text",
            name=f"SIUO Text - {question_id}",
            dataset_name=self.dataset_name,
            harm_categories=[category],
            description=self._DESCRIPTION,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.PAPER_URL,
            prompt_group_id=group_id,
            sequence=0,
            metadata=metadata,
        )

        image_prompt = SeedPrompt(
            value=local_image_path,
            data_type="image_path",
            name=f"SIUO Image - {question_id}",
            dataset_name=self.dataset_name,
            harm_categories=[category],
            description=self._DESCRIPTION,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.PAPER_URL,
            prompt_group_id=group_id,
            sequence=0,
            metadata={**metadata, "original_image_url": image_url},
        )

        return [objective, text_prompt, image_prompt]

    async def _fetch_and_save_image_async(self, *, image_url: str, image_stem: str) -> str:
        """
        Fetch and cache a SIUO image.

        Args:
            image_url (str): Full URL of the image on the HuggingFace mirror.
            image_stem (str): Filename stem (e.g. 'S-01') used to name the
                cached file.

        Returns:
            str: Local path to the cached image.

        Raises:
            Exception: Any error raised by the underlying HTTP fetch is
                propagated so the caller can skip the row.
        """
        return await fetch_and_cache_image_async(
            filename=f"siuo_{image_stem}.png",
            image_url=image_url,
            log_prefix="SIUO",
            follow_redirects=True,
        )
