# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from collections.abc import Iterable
from enum import Enum
from typing import Any, Literal, cast

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote._image_cache import (
    fetch_and_cache_image_async,
)
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class MossBenchOversensitivityType(Enum):
    """
    Oversensitivity stimulus types in the MOSSBench dataset.

    The MOSSBench paper organizes benign-but-tricky multimodal queries around
    three cognitive-science triggers that lead VLMs to refuse harmless requests:

    - ``EXAGGERATED_RISK`` — innocuous scene with a visually salient but
      contextually harmless risky-looking element (e.g. a toy knife on a
      playroom shelf).
    - ``NEGATED_HARM`` — image depicts harm in a way that is explicitly negated
      by surrounding context.
    - ``COUNTERINTUITIVE_INTERPRETATION`` — image whose obvious literal reading
      conflicts with the actual user intent.

    The raw ``metadata.over`` field in the upstream JSON uses the strings
    ``"type 1"`` / ``"type 2"`` / ``"type 3"`` for these three concepts;
    the loader maps those to the enum members.
    """

    EXAGGERATED_RISK = "exaggerated_risk"
    NEGATED_HARM = "negated_harm"
    COUNTERINTUITIVE_INTERPRETATION = "counterintuitive_interpretation"


# Mapping from the raw ``metadata.over`` string in MOSSBench's information.json
# to the loader's public enum.
_RAW_OVERSENSITIVITY_TO_ENUM: dict[str, MossBenchOversensitivityType] = {
    "type 1": MossBenchOversensitivityType.EXAGGERATED_RISK,
    "type 2": MossBenchOversensitivityType.NEGATED_HARM,
    "type 3": MossBenchOversensitivityType.COUNTERINTUITIVE_INTERPRETATION,
}

# Human-readable label per oversensitivity type (matches the paper).
_OVERSENSITIVITY_TYPE_LABELS: dict[MossBenchOversensitivityType, str] = {
    MossBenchOversensitivityType.EXAGGERATED_RISK: "Exaggerated Risk",
    MossBenchOversensitivityType.NEGATED_HARM: "Negated Harm",
    MossBenchOversensitivityType.COUNTERINTUITIVE_INTERPRETATION: "Counterintuitive Interpretation",
}


class _MossBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the MOSSBench multimodal over-sensitivity dataset.

    MOSSBench probes whether vision-language models incorrectly **refuse**
    harmless multimodal queries when the image contains a superficially
    risky-looking element. Unlike most multimodal safety benchmarks, every one
    of the 300 manually curated queries is **benign by design**: a well-behaved
    VLM should answer normally, not refuse.

    Each example is delivered as a two-piece multimodal user turn — an image
    and a benign text question — both wrapped in ``SeedPrompt`` objects
    that share a ``prompt_group_id`` and ``sequence=0``. No ``SeedObjective``
    is created (matching the convention used by the text-only over-refusal
    loaders ``_XSTestDataset`` and ``_ORBenchBaseDataset``); the
    "non-refusal expected" semantics live in the dataset's identity rather
    than in a per-row objective field.

    Notes:
        - **Image source**: GitHub raw URLs pinned to commit
          ``8d68b0614b39d8990a508e03d99975832f399db2``. All 300 PNG images are
          in the repo at predictable paths. The first call downloads each image
          once via ``fetch_and_cache_image_async``; subsequent calls reuse
          the on-disk cache.
        - **Harm indices**: the upstream ``metadata.harm`` list contains
          HarmBench-style integer indices, but the explicit
          ``index → category-label`` mapping is **not** documented in the
          paper, repo, or project page. To avoid mislabeling, this loader
          preserves the raw integers in ``SeedPrompt.metadata['harm_indices']``
          and uses the over-sensitivity-type slug as the ``harm_categories``
          entry.
        - **Image-attribute filters** (``human`` / ``child`` / ``syn`` / ``ocr``
          flags) are surfaced as per-seed metadata only; the loader does not
          expose them as constructor filters to keep the API small.

    References:
        - Paper: <https://arxiv.org/abs/2406.17806> [@li2024mossbench]
        - GitHub: <https://github.com/xirui-li/MOSSBench>
        - HF mirror: <https://huggingface.co/datasets/AIcell/MOSSBench>

    License: CC BY-SA 4.0.
    """

    _COMMIT: str = "8d68b0614b39d8990a508e03d99975832f399db2"
    METADATA_URL: str = (
        f"https://raw.githubusercontent.com/xirui-li/MOSSBench/{_COMMIT}/data/images_information/information.json"
    )
    IMAGE_BASE_URL: str = f"https://raw.githubusercontent.com/xirui-li/MOSSBench/{_COMMIT}/data/images/"
    PAPER_URL: str = "https://arxiv.org/abs/2406.17806"
    DESCRIPTION: str = (
        "MOSSBench is a multimodal over-sensitivity benchmark of 300 manually curated benign image+question "
        "pairs across three oversensitivity stimulus types (Exaggerated Risk, Negated Harm, Counterintuitive "
        "Interpretation). A well-behaved vision-language model should answer each query normally; refusing "
        "indicates over-sensitivity. The prompts are benign — the harm-category indices in metadata describe "
        "what the image superficially evokes, not the actual harm of the question."
    )
    AUTHORS: tuple[str, ...] = (
        "Xirui Li",
        "Hengguang Zhou",
        "Ruochen Wang",
        "Tianyi Zhou",
        "Minhao Cheng",
        "Cho-Jui Hsieh",
    )
    GROUPS: tuple[str, ...] = (
        "University of California, Los Angeles",
        "University of Maryland",
        "Pennsylvania State University",
    )

    tags: frozenset[str] = frozenset({"default", "safety", "multimodal", "refusal"})
    size: str = "medium"
    modalities: tuple[str, ...] = ("text", "image")
    harm_categories: tuple[str, ...] = tuple(t.value for t in MossBenchOversensitivityType)

    def __init__(
        self,
        *,
        source: str = METADATA_URL,
        source_type: Literal["public_url", "file"] = "public_url",
        oversensitivity_types: list[MossBenchOversensitivityType] | None = None,
    ) -> None:
        """
        Initialize the MOSSBench dataset loader.

        Args:
            source (str): URL or file path to the MOSSBench ``information.json``
                metadata file. Defaults to the official GitHub repository at a
                pinned commit.
            source_type (Literal["public_url", "file"]): The type of source
                (``"public_url"`` or ``"file"``).
            oversensitivity_types (list[MossBenchOversensitivityType] | None):
                Filter examples by oversensitivity stimulus type. If ``None``
                (default), all three types are included. Valid values:
                ``MossBenchOversensitivityType.EXAGGERATED_RISK``,
                ``MossBenchOversensitivityType.NEGATED_HARM``,
                ``MossBenchOversensitivityType.COUNTERINTUITIVE_INTERPRETATION``.

        Raises:
            ValueError: If any value in ``oversensitivity_types`` is not a
                ``MossBenchOversensitivityType`` member.
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        self.oversensitivity_types = oversensitivity_types

        if oversensitivity_types is not None:
            if not oversensitivity_types:
                raise ValueError(
                    "`oversensitivity_types` must be a non-empty list (pass None to include all oversensitivity types)"
                )
            self._validate_enums(
                oversensitivity_types,
                MossBenchOversensitivityType,
                "oversensitivity type",
            )

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "mossbench"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch MOSSBench examples and return them as a ``SeedDataset``.

        Each example yields two ``SeedPrompt`` objects — an image and a
        benign text question — that share a ``prompt_group_id`` and
        ``sequence=0`` so the orchestrator delivers them as a single
        multimodal user turn.

        Args:
            cache (bool): Whether to cache the fetched metadata. Defaults to
                ``True``.

        Returns:
            SeedDataset: A ``SeedDataset`` containing the multimodal
            examples.

        Raises:
            ValueError: If any example is missing required keys, or if no
                example survives the configured ``oversensitivity_types``
                filter and any image-fetch failures.
        """
        logger.info(f"Loading MOSSBench dataset from {self.source}")

        examples = self._load_examples(cache=cache)
        prompts: list[SeedUnion] = []
        failed_image_count = 0

        for example in examples:
            pid, question, image_filename = self._extract_required_fields(example)
            oversensitivity_type = self._parse_oversensitivity_type(example)
            if not self._matches_filters(oversensitivity_type):
                continue

            try:
                pair = await self._build_prompt_pair_async(
                    pid=pid,
                    question=question,
                    image_filename=image_filename,
                    example=example,
                    oversensitivity_type=oversensitivity_type,
                )
            except Exception as e:
                failed_image_count += 1
                logger.warning(f"[MOSSBench] Failed to fetch image for pid={pid}: {e}. Skipping this example.")
                continue

            prompts.extend(pair)

        if failed_image_count > 0:
            logger.warning(f"[MOSSBench] Skipped {failed_image_count} example(s) due to image fetch failures")

        if not prompts:
            raise ValueError(
                "MOSSBench SeedDataset cannot be empty. Check your filter criteria "
                "(oversensitivity_types) — all examples may have been filtered out "
                "or skipped due to image fetch failures."
            )

        logger.info(f"Successfully loaded {len(prompts)} prompts from MOSSBench dataset")
        return SeedDataset(seeds=prompts, dataset_name=self.dataset_name)

    def _load_examples(self, *, cache: bool) -> Iterable[dict[str, Any]]:
        """
        Fetch the raw MOSSBench ``information.json`` and yield example dicts.

        The upstream JSON is a dict keyed by ``pid`` (1..300); this helper
        normalizes the structure to an iterable of value dicts so the rest of
        the loader can be source-agnostic.

        Args:
            cache (bool): Whether to cache the fetched metadata locally.

        Returns:
            Iterable[dict[str, Any]]: Iterable over per-pid example dicts.

        Raises:
            ValueError: If the parsed JSON is not a dict of pid → entry.
        """
        raw = cast(
            "Any",
            self._fetch_from_url(source=self.source, source_type=self.source_type, cache=cache),
        )
        if not isinstance(raw, dict):
            raise ValueError(
                f"Expected MOSSBench information.json to be a dict keyed by pid, got {type(raw).__name__}."
            )
        return cast("Iterable[dict[str, Any]]", raw.values())

    def _matches_filters(self, oversensitivity_type: MossBenchOversensitivityType) -> bool:
        """
        Return whether an example passes the configured oversensitivity-type filter.

        Args:
            oversensitivity_type (MossBenchOversensitivityType): Parsed oversensitivity
                type for the candidate example.

        Returns:
            bool: ``True`` if the example should be included.
        """
        if self.oversensitivity_types is None:
            return True
        return oversensitivity_type in self.oversensitivity_types

    async def _build_prompt_pair_async(
        self,
        *,
        pid: str,
        question: str,
        image_filename: str,
        example: dict[str, Any],
        oversensitivity_type: MossBenchOversensitivityType,
    ) -> list[SeedPrompt]:
        """
        Build an image+text ``SeedPrompt`` pair for a single MOSSBench example.

        Args:
            pid (str): MOSSBench prompt id (used in seed names and cached image
                filename).
            question (str): Benign text question for the example.
            image_filename (str): Basename of the upstream image (e.g.
                ``"42.png"``); joined onto ``IMAGE_BASE_URL`` to form the URL.
            example (dict[str, Any]): Single example dict from the upstream
                ``information.json`` (used to extract attribute-flag metadata).
            oversensitivity_type (MossBenchOversensitivityType): Parsed
                oversensitivity type for the example.

        Returns:
            list[SeedPrompt]: A two-element list — the image prompt followed by
            the text prompt — both sharing ``prompt_group_id`` and
            ``sequence=0``.

        Raises:
            Exception: If the image cannot be fetched.
        """
        meta = self._extract_metadata(example=example, oversensitivity_type=oversensitivity_type)
        group_id = uuid.uuid4()
        image_url = f"{self.IMAGE_BASE_URL}{image_filename}"

        local_image_path = await self._fetch_and_save_image_async(image_url=image_url, pid=pid)

        image_prompt = SeedPrompt(
            value=local_image_path,
            data_type="image_path",
            name=f"MOSSBench Image - {pid}",
            dataset_name=self.dataset_name,
            harm_categories=[oversensitivity_type.value],
            description=self.DESCRIPTION,
            authors=list(self.AUTHORS),
            groups=list(self.GROUPS),
            source=self.PAPER_URL,
            prompt_group_id=group_id,
            sequence=0,
            metadata={**meta, "original_image_url": image_url},
        )

        text_prompt = SeedPrompt(
            value=question,
            data_type="text",
            name=f"MOSSBench Text - {pid}",
            dataset_name=self.dataset_name,
            harm_categories=[oversensitivity_type.value],
            description=self.DESCRIPTION,
            authors=list(self.AUTHORS),
            groups=list(self.GROUPS),
            source=self.PAPER_URL,
            prompt_group_id=group_id,
            sequence=0,
            metadata=meta,
        )

        return [image_prompt, text_prompt]

    @staticmethod
    def _extract_required_fields(example: dict[str, Any]) -> tuple[str, str, str]:
        """
        Pull ``pid``, ``question``, and ``image`` filename from a raw example.

        Args:
            example (dict[str, Any]): Single example dict from the upstream
                ``information.json``.

        Returns:
            tuple[str, str, str]: ``(pid, question, image_filename)`` where
            ``image_filename`` is the basename (e.g. ``"42.png"``).

        Raises:
            ValueError: If any of the required keys is missing.
        """
        required = {"pid", "question", "image"}
        missing = required - example.keys()
        if missing:
            raise ValueError(f"Missing keys in MOSSBench example: {', '.join(sorted(missing))}")

        pid = str(example["pid"])
        question = str(example["question"])
        # ``image`` looks like "images/42.png"; we only need the basename so we
        # can join it onto IMAGE_BASE_URL ourselves.
        image_filename = str(example["image"]).rsplit("/", 1)[-1]
        return pid, question, image_filename

    @staticmethod
    def _parse_oversensitivity_type(example: dict[str, Any]) -> MossBenchOversensitivityType:
        """
        Map the raw ``metadata.over`` string to a ``MossBenchOversensitivityType``.

        Args:
            example (dict[str, Any]): Single example dict from the upstream
                ``information.json``.

        Returns:
            MossBenchOversensitivityType: The parsed oversensitivity type.

        Raises:
            ValueError: If ``metadata.over`` is missing or not one of the three
                known values.
        """
        meta = example.get("metadata") or {}
        raw_over = meta.get("over")
        enum_value = _RAW_OVERSENSITIVITY_TO_ENUM.get(raw_over) if isinstance(raw_over, str) else None
        if enum_value is None:
            valid = ", ".join(sorted(_RAW_OVERSENSITIVITY_TO_ENUM))
            raise ValueError(
                f"MOSSBench example pid={example.get('pid', '?')} has unknown over type "
                f"{raw_over!r}; expected one of: {valid}."
            )
        return enum_value

    @staticmethod
    def _extract_metadata(
        *,
        example: dict[str, Any],
        oversensitivity_type: MossBenchOversensitivityType,
    ) -> dict[str, Any]:
        """
        Build the per-seed metadata dict, preserving all upstream attribute flags.

        Args:
            example (dict[str, Any]): Single example dict from the upstream
                ``information.json``.
            oversensitivity_type (MossBenchOversensitivityType): Parsed
                oversensitivity type for the example.

        Returns:
            dict[str, Any]: Metadata dict including ``pid``, the
            oversensitivity-type slug + label, and the raw image-attribute flags
            (``human``, ``child``, ``syn``, ``ocr``, ``harm_indices``).
        """
        meta = example.get("metadata") or {}
        harm_raw = meta.get("harm") or []
        harm_indices = [int(h) for h in harm_raw if isinstance(h, (int, float, str)) and str(h).lstrip("-").isdigit()]
        return {
            "pid": str(example["pid"]),
            "oversensitivity_type": oversensitivity_type.value,
            "oversensitivity_type_label": _OVERSENSITIVITY_TYPE_LABELS[oversensitivity_type],
            "human": bool(meta.get("human", 0)),
            "child": bool(meta.get("child", 0)),
            "syn": bool(meta.get("syn", 0)),
            "ocr": bool(meta.get("ocr", 0)),
            "harm_indices": harm_indices,
            "short_description": str(example.get("short description", "")),
        }

    async def _fetch_and_save_image_async(self, *, image_url: str, pid: str) -> str:
        """
        Fetch and cache a MOSSBench image.

        Args:
            image_url (str): URL to the image PNG.
            pid (str): MOSSBench prompt id, used to name the cached file.

        Returns:
            str: Local path to the cached image.
        """
        return await fetch_and_cache_image_async(
            filename=f"mossbench_{pid}.png",
            image_url=image_url,
            log_prefix="MOSSBench",
        )
