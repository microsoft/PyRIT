# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import logging
import os
import uuid
import zipfile
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from huggingface_hub import hf_hub_download
from typing_extensions import override

from pyrit.common.path import DB_DATA_PATH
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class VLGuardCategory(Enum):
    """
    Categories in the VLGuard dataset.

    PRIVACY: Content involving personal data exposure or surveillance (e.g., reading IDs, tracking individuals).
    RISKY_BEHAVIOR: Content depicting or encouraging dangerous activities (e.g., violence, professional advice).
    DECEPTION: Content related to misleading or false information (e.g., disinformation, political manipulation).
    DISCRIMINATION: Content targeting groups based on identity (e.g., discrimination by sex or race).
    """

    PRIVACY = "privacy"
    RISKY_BEHAVIOR = "risky behavior"
    DECEPTION = "deception"
    DISCRIMINATION = "discrimination"


class VLGuardSubcategory(Enum):
    """
    Subcategories in the VLGuard dataset. Each subcategory belongs to a specific category.

    privacy: personal data
    risky behavior: professional advice, political, sexually explicit, violence
    deception: disinformation
    discrimination: sex, race, other
    """

    PERSONAL_DATA = "personal data"
    PROFESSIONAL_ADVICE = "professional advice"
    POLITICAL = "political"
    SEXUALLY_EXPLICIT = "sexually explicit"
    VIOLENCE = "violence"
    DISINFORMATION = "disinformation"
    SEX = "sex"
    RACE = "race"
    OTHER = "other"


class VLGuardSubset(Enum):
    """
    Evaluation subsets in the VLGuard dataset.

    UNSAFES: Unsafe images with instructions — tests whether the model refuses unsafe visual content.
    SAFE_UNSAFES: Safe images with unsafe instructions — tests whether the model refuses unsafe text prompts.
    SAFE_SAFES: Safe images with safe instructions — tests whether the model remains helpful.
    """

    UNSAFES = "unsafes"
    SAFE_UNSAFES = "safe_unsafes"
    SAFE_SAFES = "safe_safes"


class _VLGuardDataset(_RemoteDatasetLoader):
    """
    Loader for the VLGuard multimodal safety dataset.

    VLGuard contains image-instruction pairs for evaluating vision-language model safety.
    It includes both unsafe and safe images paired with various instructions to test whether
    models refuse unsafe content while remaining helpful on safe content.

    The dataset covers 4 categories (privacy, risky behavior, deception, discrimination)
    with 9 subcategories (personal data, professional advice, political, sexually explicit,
    violence, disinformation, sex, race, other).

    Note: This is a gated dataset on HuggingFace. You must accept the terms at
    https://huggingface.co/datasets/ys-zong/VLGuard before use, and provide
    a HuggingFace token.

    Reference: [@zong2024vlguard]
    Paper: Safety Fine-Tuning at (Almost) No Cost: A Baseline for Vision Large Language Models (ICML 2024)
    """

    _HF_REPO_ID: ClassVar[str] = "ys-zong/VLGuard"

    _AUTHORS = [
        "Yongshuo Zong",
        "Ondrej Bohdal",
        "Tingyang Yu",
        "Yongxin Yang",
        "Timothy Hospedales",
    ]

    _GROUPS = ["University of Edinburgh", "EPFL"]

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT, Modality.IMAGE)
    size: str = "large"  # 884 image-instruction pairs across 4 categories
    tags: frozenset[str] = frozenset({"safety", "multimodal"})

    def __init__(
        self,
        *,
        subset: VLGuardSubset = VLGuardSubset.UNSAFES,
        categories: list[VLGuardCategory] | None = None,
        token: str | None = None,
    ) -> None:
        """
        Initialize the VLGuard dataset loader.

        Args:
            subset (VLGuardSubset): Which evaluation subset to load. Defaults to UNSAFES.
            categories (list[VLGuardCategory] | None): List of VLGuard categories to filter by.
                If None, all categories are included.
            token (str | None): HuggingFace authentication token for accessing the gated dataset.
                If not provided, reads from the ``HUGGINGFACE_TOKEN`` environment variable.

        Raises:
            ValueError: If any of the specified categories are invalid.
        """
        self.subset = subset
        self.categories = categories
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")
        self.source = f"https://huggingface.co/datasets/{self._HF_REPO_ID}"

        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            valid_categories = {cat.value for cat in VLGuardCategory}
            invalid_categories = {
                cat.value if isinstance(cat, VLGuardCategory) else cat for cat in categories
            } - valid_categories
            if invalid_categories:
                raise ValueError(f"Invalid VLGuard categories: {', '.join(invalid_categories)}")

    @property
    @override
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "vlguard"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch VLGuard multimodal examples and return as SeedDataset.

        Downloads the test split metadata and images from HuggingFace, then creates
        multimodal prompts (text + image pairs linked by prompt_group_id and sequence=0) based on
        the selected subset.

        Args:
            cache (bool): Whether to cache downloaded files. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the multimodal examples.
        """
        logger.info(f"Loading VLGuard dataset (subset={self.subset.value})")

        metadata, image_dir = await self._download_dataset_files_async(cache=cache)

        prompts: list[SeedUnion] = []

        for example in metadata:
            image_filename = example.get("image")
            is_safe = example.get("safe", True)
            category = example.get("harmful_category", "")
            subcategory = example.get("harmful_subcategory", "")
            instr_resp_raw = example.get("instr-resp")
            if not instr_resp_raw or not isinstance(instr_resp_raw, list):
                continue
            instr_resp: list[dict[str, str]] = instr_resp_raw

            if not image_filename:
                continue

            # Filter by subset (safe flag)
            if self.subset == VLGuardSubset.UNSAFES and is_safe:
                continue
            if self.subset in (VLGuardSubset.SAFE_UNSAFES, VLGuardSubset.SAFE_SAFES) and not is_safe:
                continue

            # Filter by categories
            if self.categories is not None:
                category_values = {cat.value for cat in self.categories}
                if category not in category_values:
                    continue

            instruction = self._extract_instruction(instr_resp)
            if not instruction:
                continue

            image_path = image_dir / image_filename
            if not image_path.exists():
                logger.warning(f"Image not found: {image_path}")
                continue

            group_id = uuid.uuid4()

            text_prompt = SeedPrompt(
                value=instruction,
                data_type="text",
                name="VLGuard Text",
                dataset_name=self.dataset_name,
                harm_categories=[category],
                description=f"Text component of VLGuard multimodal prompt ({self.subset.value}).",
                source=self.source,
                prompt_group_id=group_id,
                sequence=0,
                metadata={
                    "category": category,
                    "subcategory": subcategory,
                    "subset": self.subset.value,
                    "safe_image": is_safe,
                },
                authors=self._AUTHORS,
                groups=self._GROUPS,
            )

            image_prompt = SeedPrompt(
                value=str(image_path),
                data_type="image_path",
                name="VLGuard Image",
                dataset_name=self.dataset_name,
                harm_categories=[category],
                description=f"Image component of VLGuard multimodal prompt ({self.subset.value}).",
                source=self.source,
                prompt_group_id=group_id,
                sequence=0,
                metadata={
                    "category": category,
                    "subcategory": subcategory,
                    "subset": self.subset.value,
                    "safe_image": is_safe,
                    "original_filename": image_filename,
                },
                authors=self._AUTHORS,
                groups=self._GROUPS,
            )

            prompts.append(text_prompt)
            prompts.append(image_prompt)

        logger.info(f"Successfully loaded {len(prompts)} prompts from VLGuard dataset ({self.subset.value})")

        return SeedDataset(seeds=prompts, dataset_name=self.dataset_name)

    def _extract_instruction(self, instr_resp: list[dict[str, str]]) -> str | None:
        """
        Extract the instruction text from an example based on the current subset.

        Args:
            instr_resp (list[dict[str, str]]): List of instruction-response dictionaries from VLGuard.

        Returns:
            str | None: The instruction text, or None if not found for the given subset.
        """
        if self.subset == VLGuardSubset.UNSAFES:
            if instr_resp and "instruction" in instr_resp[0]:
                return str(instr_resp[0]["instruction"])
        elif self.subset == VLGuardSubset.SAFE_UNSAFES:
            for item in instr_resp:
                if "unsafe_instruction" in item:
                    return str(item["unsafe_instruction"])
        elif self.subset == VLGuardSubset.SAFE_SAFES:
            for item in instr_resp:
                if "safe_instruction" in item:
                    return str(item["safe_instruction"])
        return None

    async def _download_dataset_files_async(self, *, cache: bool = True) -> tuple[list[dict[str, str]], Path]:
        """
        Download VLGuard metadata and images from HuggingFace.

        Args:
            cache (bool): Whether to use cached files if available.

        Returns:
            tuple[list[dict], Path]: Tuple of (metadata list, image directory path).
        """
        cache_dir = DB_DATA_PATH / "seed-prompt-entries" / "vlguard"
        cache_dir.mkdir(parents=True, exist_ok=True)

        json_path = cache_dir / "test.json"
        image_dir = cache_dir / "test"

        # Use cache if available
        if cache and json_path.exists() and image_dir.exists() and any(image_dir.iterdir()):
            logger.info("Using cached VLGuard dataset")
            with open(json_path, encoding="utf-8") as f:
                metadata = json.load(f)
            return metadata, image_dir

        logger.info("Downloading VLGuard dataset from HuggingFace...")

        def _download_sync() -> tuple[str, str]:
            json_file = hf_hub_download(
                repo_id=self._HF_REPO_ID,
                filename="test.json",
                repo_type="dataset",
                local_dir=str(cache_dir),
                token=self.token,
            )
            zip_file = hf_hub_download(
                repo_id=self._HF_REPO_ID,
                filename="test.zip",
                repo_type="dataset",
                local_dir=str(cache_dir),
                token=self.token,
            )
            return json_file, zip_file

        await asyncio.to_thread(_download_sync)

        # Extract images from zip
        zip_path = cache_dir / "test.zip"
        if zip_path.exists():
            logger.info("Extracting VLGuard test images...")
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(str(cache_dir))

        with open(json_path, encoding="utf-8") as f:
            metadata = json.load(f)

        return metadata, image_dir
