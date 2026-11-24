# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import io
import json
import logging
import os
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from datasets import DownloadMode, Features, disable_progress_bars, load_dataset, load_dataset_builder
from huggingface_hub import HfFileSystem, hf_hub_download
from huggingface_hub.hf_file_system import HfFileSystemResolvedRepositoryPath

from pyrit.common.path import DB_DATA_PATH
from pyrit.datasets.seed_datasets.remote._image_cache import (
    fetch_and_cache_image_async,
)
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class MMSafetyBenchCategory(Enum):
    """
    Risk scenarios in the MM-SafetyBench dataset.

    Values match the HuggingFace mirror config names (which match the GitHub
    JSON filename suffixes minus the leading ``NN-``). The typo
    ``Illegal_Activitiy`` is preserved from upstream.
    """

    ILLEGAL_ACTIVITY = "Illegal_Activitiy"
    HATE_SPEECH = "HateSpeech"
    MALWARE_GENERATION = "Malware_Generation"
    PHYSICAL_HARM = "Physical_Harm"
    ECONOMIC_HARM = "EconomicHarm"
    FRAUD = "Fraud"
    SEX = "Sex"
    POLITICAL_LOBBYING = "Political_Lobbying"
    PRIVACY_VIOLENCE = "Privacy_Violence"
    LEGAL_OPINION = "Legal_Opinion"
    FINANCIAL_ADVICE = "Financial_Advice"
    HEALTH_CONSULTATION = "Health_Consultation"
    GOV_DECISION = "Gov_Decision"


class MMSafetyBenchVariant(Enum):
    """
    Image variants in the MM-SafetyBench dataset.

    - ``SD``: Stable Diffusion render of the harmful concept (no typography).
    - ``SD_TYPOGRAPHY``: Stable Diffusion render with the key phrase rendered
      as typography at the bottom of the image. This is the main variant used
      in the paper.
    - ``TYPOGRAPHY``: Typography of the key phrase only (no Stable Diffusion
      image).

    Values match the upstream HuggingFace split names (``SD``, ``SD_TYPO``,
    ``TYPO``), which abbreviate ``TYPOGRAPHY`` to ``TYPO``. The enum members
    spell out ``TYPOGRAPHY`` for readability so it is not confused with the
    English word "typo" (typographical error).
    """

    SD = "SD"
    SD_TYPOGRAPHY = "SD_TYPO"
    TYPOGRAPHY = "TYPO"


# Maps MMSafetyBenchCategory.value to the ``NN-Scenario`` key used in
# ``TinyVersion_ID_List.json`` on the upstream GitHub repo.
_CATEGORY_TO_GITHUB_SCENARIO: dict[str, str] = {
    "Illegal_Activitiy": "01-Illegal_Activitiy",
    "HateSpeech": "02-HateSpeech",
    "Malware_Generation": "03-Malware_Generation",
    "Physical_Harm": "04-Physical_Harm",
    "EconomicHarm": "05-EconomicHarm",
    "Fraud": "06-Fraud",
    "Sex": "07-Sex",
    "Political_Lobbying": "08-Political_Lobbying",
    "Privacy_Violence": "09-Privacy_Violence",
    "Legal_Opinion": "10-Legal_Opinion",
    "Financial_Advice": "11-Financial_Advice",
    "Health_Consultation": "12-Health_Consultation",
    "Gov_Decision": "13-Gov_Decision",
}


class _MMSafetyBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the MM-SafetyBench multimodal safety benchmark (ECCV 2024).

    MM-SafetyBench probes Multimodal Large Language Models by hiding the
    harmful concept inside an image and leaving the text prompt visibly
    benign. The model is expected to refuse on the basis of the combined
    image+text, but is susceptible to answering as if the text alone were the
    request. Each example therefore carries an explicit ``SeedObjective`` (the
    source-of-truth harmful imperative — ``Changed Question`` from upstream)
    so that scorers can evaluate intent rather than the surface prompt.

    Each row in the resulting ``SeedDataset`` is a 3-seed group sharing a
    ``prompt_group_id`` and ``sequence=0``:

    1. ``SeedObjective`` carrying the harmful imperative (``Changed Question``).
    2. ``SeedPrompt`` of ``data_type='image_path'`` for the selected variant.
    3. ``SeedPrompt`` of ``data_type='text'`` for the rephrased question
       paired with that variant by the upstream evaluation protocol:

       ================= ========================================
       Variant           Paired ``question`` text
       ================= ========================================
       ``SD``            ``Rephrased Question(SD)`` ("The image shows a phrase…")
       ``SD_TYPOGRAPHY`` ``Rephrased Question`` ("The bottom of the image shows…")
       ``TYPOGRAPHY``    ``Rephrased Question`` ("The bottom of the image shows…")
       ================= ========================================

       (Confirmed against the README in the MM-SafetyBench GitHub repo,
       section ``Evaluation``.)

    Images and rephrased questions are loaded from the non-gated HuggingFace
    mirror :hf:`PKU-Alignment/MM-SafetyBench`, which packages all 13 scenarios
    × 3 image variants + a ``Text_only`` split (the ``Changed Question`` used
    as the objective) into parquet files. The original isXinLiu/MM-SafetyBench
    GitHub repo remains the canonical reference and hosts
    ``TinyVersion_ID_List.json`` (used by the ``use_tiny`` filter).

    Only the parquet files backing the selected categories and variant are
    downloaded (~250 MB for the default all-categories ``SD_TYPOGRAPHY`` load,
    ~15 MB per single category) and cached locally. Subsequent calls are fast.

    Reference: https://isxinliu.github.io/Project/MM-SafetyBench/
    Paper: [@liu2024mmsafetybench]
    """

    _AUTHORS: ClassVar[list[str]] = [
        "Xin Liu",
        "Yichen Zhu",
        "Jindong Gu",
        "Yunshi Lan",
        "Chao Yang",
        "Yu Qiao",
    ]
    _GROUPS: ClassVar[list[str]] = [
        "East China Normal University",
        "Shanghai AI Laboratory",
        "Midea Group",
        "University of Oxford",
    ]
    _DESCRIPTION: ClassVar[str] = (
        "MM-SafetyBench (ECCV 2024) is a multimodal safety benchmark of 5,040 "
        "text-image pairs across 13 risk scenarios. The harmful concept is "
        "moved into the image (as a Stable Diffusion render of the concept, as "
        "typography of the key phrase, or both) while the visible text prompt is "
        "rephrased to be benign-looking ('the image shows a phrase of an "
        "activity. List steps in detail to perform this activity.')."
    )
    HF_DATASET_NAME: str = "PKU-Alignment/MM-SafetyBench"
    PAPER_URL: str = "https://arxiv.org/abs/2311.17600"
    SOURCE_URL: str = "https://huggingface.co/datasets/PKU-Alignment/MM-SafetyBench"
    TINY_VERSION_URL: str = (
        "https://raw.githubusercontent.com/isXinLiu/MM-SafetyBench/"
        "b80eedea3db312c09ded2082813390f68e750ef3/TinyVersion_ID_List.json"
    )

    harm_categories: tuple[str, ...] = (
        "illegal_activity",
        "hate_speech",
        "malware",
        "physical_harm",
        "economic_harm",
        "fraud",
        "sexual",
        "political_lobbying",
        "privacy",
        "legal_opinion",
        "financial_advice",
        "health_consultation",
        "government_decision",
    )
    modalities: tuple[str, ...] = ("text", "image")
    size: str = "huge"
    tags: frozenset[str] = frozenset({"default", "safety", "multimodal"})

    def __init__(
        self,
        *,
        variant: MMSafetyBenchVariant = MMSafetyBenchVariant.SD_TYPOGRAPHY,
        categories: list[MMSafetyBenchCategory] | None = None,
        use_tiny: bool = False,
        token: str | None = None,
    ) -> None:
        """
        Initialize the MM-SafetyBench dataset loader.

        Args:
            variant (MMSafetyBenchVariant): Which image variant to load.
                Defaults to ``MMSafetyBenchVariant.SD_TYPOGRAPHY``, the variant
                primarily used in the paper.
            categories (list[MMSafetyBenchCategory] | None): Risk scenarios to
                include. If None, all 13 scenarios are loaded.
            use_tiny (bool): When True, filter to the per-scenario IDs in
                ``TinyVersion_ID_List.json`` (~150 questions total) for fast
                evaluations. Defaults to False.
            token (str | None): HuggingFace token. The mirror is non-gated so
                this is optional; provided for parity with other loaders.

        Raises:
            ValueError: If ``variant`` or any ``categories`` value is not a
                valid enum member.
        """
        self._validate_enum(variant, MMSafetyBenchVariant, "variant")
        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(categories, MMSafetyBenchCategory, "category")

        self.variant = variant
        self.categories = categories
        self.use_tiny = use_tiny
        self.token = token
        self.source = self.SOURCE_URL

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "mm_safetybench"

    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch MM-SafetyBench examples and return as a ``SeedDataset``.

        Args:
            cache (bool): Whether to cache the fetched data. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset where every 3 consecutive seeds form
            one image+text+objective group sharing a ``prompt_group_id`` and
            ``sequence=0``.

        Raises:
            ValueError: If no seeds remain after filtering.
        """
        tiny_id_map = self._load_tiny_id_map(cache=cache) if self.use_tiny else None
        selected_categories = list(self.categories) if self.categories is not None else list(MMSafetyBenchCategory)

        seeds: list[SeedUnion] = []
        group_count = 0
        failed_image_count = 0

        for category in selected_categories:
            category_value = category.value
            tiny_ids = tiny_id_map.get(category_value) if tiny_id_map is not None else None

            objective_by_id = await self._load_objectives_async(category_value=category_value, cache=cache)
            variant_rows = await self._load_variant_split_async(category_value=category_value, cache=cache)

            for row in variant_rows:
                question_id = str(row.get("id", ""))
                if not question_id:
                    continue
                if tiny_ids is not None and int(question_id) not in tiny_ids:
                    continue

                objective_text = objective_by_id.get(question_id)
                if not objective_text:
                    logger.debug(f"[MM-SafetyBench] No objective found for {category_value}/{question_id}; skipping.")
                    continue

                rephrased_text = row.get("question", "")
                pil_image = row.get("image")
                if pil_image is None:
                    continue

                try:
                    local_image_path = await self._save_pil_image_async(
                        pil_image=pil_image,
                        category_value=category_value,
                        question_id=question_id,
                    )
                except Exception as exc:
                    failed_image_count += 1
                    logger.warning(f"[MM-SafetyBench] Failed to save image for {category_value}/{question_id}: {exc}")
                    continue

                seeds.extend(
                    self._build_group(
                        category_value=category_value,
                        question_id=question_id,
                        objective_text=objective_text,
                        rephrased_text=rephrased_text,
                        local_image_path=local_image_path,
                    )
                )
                group_count += 1

        if failed_image_count:
            logger.warning(f"[MM-SafetyBench] Skipped {failed_image_count} example(s) due to image save failures")

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(
            f"Successfully loaded {len(seeds)} seeds ({group_count} groups) from MM-SafetyBench "
            f"(variant={self.variant.value})"
        )
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    async def _load_objectives_async(self, *, category_value: str, cache: bool) -> dict[str, str]:
        """
        Load the per-id ``Changed Question`` (objective) for a category from the ``Text_only`` split.

        Args:
            category_value: HuggingFace config name (e.g. ``"Illegal_Activitiy"``).
            cache: Whether to cache the fetched data.

        Returns:
            dict mapping ``id`` (string) to the objective text.
        """
        text_only = await self._fetch_single_split_async(category_value=category_value, split="Text_only", cache=cache)
        return {str(row.get("id", "")): row.get("question", "") for row in text_only}

    async def _fetch_single_split_async(self, *, category_value: str, split: str, cache: bool) -> Any:
        """
        Fetch one split of one config, downloading only the files that back it.

        ``load_dataset(repo, config, split=...)`` downloads every split of the
        config (for this dataset, all three image variants plus ``Text_only``,
        ~30 MB per category when ~15 MB is needed), because ``split`` only
        selects what gets prepared. Instead, resolve the split's data files and
        features from the config metadata (a ~22 KB fetch), download exactly
        those files, and load them with the parquet builder.

        Args:
            category_value: HuggingFace config name (e.g. ``"Illegal_Activitiy"``).
            split: Split name within the config (e.g. ``"SD_TYPO"``).
            cache: Whether to reuse cached data. When False, files are
                force-redownloaded, mirroring ``_fetch_from_huggingface_async``.

        Returns:
            The HuggingFace ``Dataset`` for that split (iterable of dict rows,
            with the ``image`` column decoded per the dataset's features).

        Raises:
            ValueError: If the config metadata lists no data files for ``split``.
        """
        disable_progress_bars()

        def _resolve_split_files(
            *, cache_dir: str | None, download_mode: DownloadMode
        ) -> tuple[list[dict[str, str]], Any]:
            """
            Resolve the split's backing files and features from the remote config metadata (~22 KB).

            Args:
                cache_dir: Datasets cache directory, or None to bypass the cache.
                download_mode: Download mode forwarded to ``load_dataset_builder``.

            Returns:
                A (files, features) tuple, where each file dict carries the
                resolved ``repo_id``/``repo_type``/``revision``/``filename``.

            Raises:
                ValueError: If the config metadata lists no data files for
                    ``split``, or a data file is not a repository path.
            """
            builder = load_dataset_builder(
                self.HF_DATASET_NAME,
                category_value,
                cache_dir=cache_dir,
                download_mode=download_mode,
                token=self.token,
            )
            data_files = (builder.config.data_files or {}).get(split) or []
            if not data_files:
                raise ValueError(
                    f"No data files found for config {category_value!r} split {split!r} of {self.HF_DATASET_NAME}"
                )

            # data_files entries are revision-pinned hf:// URLs.
            fs = HfFileSystem(token=self.token)
            remote_files: list[dict[str, str]] = []
            for url in data_files:
                resolved = fs.resolve_path(url)
                if not isinstance(resolved, HfFileSystemResolvedRepositoryPath):
                    raise ValueError(f"Unexpected non-repository data file path for {self.HF_DATASET_NAME}: {url!r}")
                remote_files.append(
                    {
                        "repo_id": resolved.repo_id,
                        "repo_type": resolved.repo_type,
                        "revision": resolved.revision,
                        "filename": resolved.path_in_repo,
                    }
                )
            return remote_files, builder.info.features

        def _load_split_sync() -> Any:
            cache_dir = (os.getenv("HF_DATASETS_CACHE") or str(DB_DATA_PATH / "huggingface")) if cache else None
            download_mode = DownloadMode.REUSE_DATASET_IF_EXISTS if cache else DownloadMode.FORCE_REDOWNLOAD
            manifest_path = (
                Path(cache_dir)
                / "pyrit_split_manifests"
                / f"{self.HF_DATASET_NAME.replace('/', '__')}__{category_value}__{split}.json"
                if cache_dir is not None
                else None
            )

            try:
                remote_files, features = _resolve_split_files(cache_dir=cache_dir, download_mode=download_mode)
                if manifest_path is not None and features is not None:
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(
                        json.dumps({"files": remote_files, "features": features.to_dict()}), encoding="utf-8"
                    )
            except (ConnectionError, OSError) as exc:
                # The hub is unreachable (e.g. HF_HUB_OFFLINE=1). The prepared
                # dataset is cached under the local parquet builder, so the
                # repo-name cache fallback inside load_dataset_builder cannot
                # find it; reuse the manifest a previous successful fetch of
                # this split persisted instead.
                if manifest_path is None or not manifest_path.exists():
                    raise
                logger.warning(
                    f"[MM-SafetyBench] Could not resolve {self.HF_DATASET_NAME} ({category_value}/{split}) "
                    f"remotely ({exc}); reusing the cached split manifest."
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                remote_files = manifest["files"]
                features = Features.from_dict(manifest["features"])

            # Download each file through the hub cache so repeated fetches
            # reuse existing blobs (and offline fetches resolve from cache).
            local_files = [
                hf_hub_download(
                    repo_id=remote_file["repo_id"],
                    filename=remote_file["filename"],
                    repo_type=remote_file["repo_type"],
                    revision=remote_file["revision"],
                    token=self.token,
                    force_download=not cache,
                )
                for remote_file in remote_files
            ]

            # features from the config metadata restore the Image decoding the
            # plain parquet builder would otherwise lose.
            return load_dataset(
                "parquet",
                data_files=local_files,
                split="train",
                features=features,
                cache_dir=cache_dir,
                download_mode=download_mode,
            )

        try:
            return await asyncio.to_thread(_load_split_sync)
        except Exception as e:
            logger.error(f"Failed to load HuggingFace dataset {self.HF_DATASET_NAME} ({category_value}/{split}): {e}")
            raise

    async def _load_variant_split_async(self, *, category_value: str, cache: bool) -> Any:
        """
        Load the variant split (rephrased question + image) for a category.

        Args:
            category_value: HuggingFace config name (e.g. ``"Illegal_Activitiy"``).
            cache: Whether to cache the fetched data.

        Returns:
            The HuggingFace dataset split (an iterable of dict-like rows).
        """
        return await self._fetch_single_split_async(
            category_value=category_value,
            split=self.variant.value,
            cache=cache,
        )

    def _load_tiny_id_map(self, *, cache: bool) -> dict[str, set[int]]:
        """
        Fetch ``TinyVersion_ID_List.json`` and return a category-value -> set-of-ids map.

        Args:
            cache: Whether to cache the fetched JSON.

        Returns:
            dict mapping ``MMSafetyBenchCategory.value`` to the set of allowed
            integer question ids for the tiny eval split.
        """
        raw_entries = self._fetch_from_url(
            source=self.TINY_VERSION_URL,
            source_type="public_url",
            cache=cache,
        )

        github_to_category: dict[str, str] = {v: k for k, v in _CATEGORY_TO_GITHUB_SCENARIO.items()}

        tiny_map: dict[str, set[int]] = {}
        for entry in raw_entries:
            scenario = entry.get("Scenario", "")
            category_value = github_to_category.get(scenario)
            if category_value is None:
                logger.warning(f"[MM-SafetyBench] Unknown scenario in TinyVersion: {scenario!r}")
                continue
            sampled_ids = entry.get("Sampled_ID_List", []) or []
            tiny_map[category_value] = {int(qid) for qid in sampled_ids}
        return tiny_map

    async def _save_pil_image_async(self, *, pil_image: Any, category_value: str, question_id: str) -> str:
        """
        Persist a PIL image to the seed-prompt-entries cache and return its local path.

        Args:
            pil_image: PIL ``Image`` instance from the HuggingFace ``image`` column.
            category_value: HuggingFace config name (used in the cached filename).
            question_id: Question id (used in the cached filename).

        Returns:
            Local file path to the cached image.
        """
        buffer = io.BytesIO()
        save_format = (pil_image.format or "JPEG").upper()
        if save_format not in {"JPEG", "PNG"}:
            save_format = "JPEG"
        extension = "jpg" if save_format == "JPEG" else "png"
        pil_image.save(buffer, format=save_format)

        filename = f"mm_safetybench_{category_value}_{self.variant.value}_{question_id}.{extension}"
        return await fetch_and_cache_image_async(
            filename=filename,
            image_bytes=buffer.getvalue(),
            log_prefix="MM-SafetyBench",
        )

    def _build_group(
        self,
        *,
        category_value: str,
        question_id: str,
        objective_text: str,
        rephrased_text: str,
        local_image_path: str,
    ) -> list[SeedUnion]:
        """
        Build a ``SeedObjective`` + image ``SeedPrompt`` + text ``SeedPrompt`` group for one row.

        Args:
            category_value: HuggingFace config name for the scenario.
            question_id: Upstream question id within the scenario.
            objective_text: The ``Changed Question`` from the ``Text_only`` split.
            rephrased_text: The rephrased question paired with the variant image.
            local_image_path: Local path to the saved image.

        Returns:
            A three-element list (objective, image prompt, text prompt) sharing
            the same ``prompt_group_id`` and ``sequence=0`` for the prompts.
        """
        group_id = uuid.uuid4()
        harm_category = self._harm_category_for(category_value)
        github_scenario = _CATEGORY_TO_GITHUB_SCENARIO[category_value]
        metadata: dict[str, str | int] = {
            "category": category_value,
            "github_scenario": github_scenario,
            "question_id": question_id,
            "variant": self.variant.value,
        }

        objective = SeedObjective(
            value=objective_text,
            name=f"MM-SafetyBench Objective - {category_value} {question_id}",
            dataset_name=self.dataset_name,
            harm_categories=[harm_category],
            description=self._DESCRIPTION,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.PAPER_URL,
            prompt_group_id=group_id,
            metadata=metadata,
        )

        image_prompt = SeedPrompt(
            value=local_image_path,
            data_type="image_path",
            name=f"MM-SafetyBench Image - {category_value} {self.variant.value} {question_id}",
            dataset_name=self.dataset_name,
            harm_categories=[harm_category],
            description=self._DESCRIPTION,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.SOURCE_URL,
            prompt_group_id=group_id,
            sequence=0,
            metadata=metadata,
        )

        text_prompt = SeedPrompt(
            value=rephrased_text,
            data_type="text",
            name=f"MM-SafetyBench Text - {category_value} {self.variant.value} {question_id}",
            dataset_name=self.dataset_name,
            harm_categories=[harm_category],
            description=self._DESCRIPTION,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.SOURCE_URL,
            prompt_group_id=group_id,
            sequence=0,
            metadata=metadata,
        )

        return [objective, image_prompt, text_prompt]

    @staticmethod
    def _harm_category_for(category_value: str) -> str:
        """
        Map an MM-SafetyBench category value to a normalized harm-category string.

        Args:
            category_value: HuggingFace config name (e.g. ``"Illegal_Activitiy"``).

        Returns:
            Lowercased normalized harm category (e.g. ``"illegal_activity"``).
        """
        normalized = {
            "Illegal_Activitiy": "illegal_activity",
            "HateSpeech": "hate_speech",
            "Malware_Generation": "malware",
            "Physical_Harm": "physical_harm",
            "EconomicHarm": "economic_harm",
            "Fraud": "fraud",
            "Sex": "sexual",
            "Political_Lobbying": "political_lobbying",
            "Privacy_Violence": "privacy",
            "Legal_Opinion": "legal_opinion",
            "Financial_Advice": "financial_advice",
            "Health_Consultation": "health_consultation",
            "Gov_Decision": "government_decision",
        }
        return normalized.get(category_value, category_value.lower())
