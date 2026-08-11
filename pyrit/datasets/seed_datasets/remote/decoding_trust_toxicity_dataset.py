# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Any, cast

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


# Pinned commit SHA of AI-secure/DecodingTrust `main` (2024-09-16).
# Pinning prevents silent upstream changes from altering the prompt set.
_DECODING_TRUST_COMMIT = "161ae8321ced62f45fcd9ceb412e05b47c603cd4"
_BASE_URL = (
    f"https://raw.githubusercontent.com/AI-secure/DecodingTrust/{_DECODING_TRUST_COMMIT}/data/toxicity/user_prompts/"
)
_TOXIC_URL = f"{_BASE_URL}toxic.jsonl"
_NONTOXIC_URL = f"{_BASE_URL}nontoxic.jsonl"

# Mapping from Perspective API score keys (as published in the DecodingTrust JSONL)
# to harm_categories strings exposed on each SeedPrompt.
_PERSPECTIVE_SCORE_KEYS: tuple[str, ...] = (
    "toxicity",
    "severe_toxicity",
    "profanity",
    "sexually_explicit",
    "identity_attack",
    "threat",
    "insult",
    "flirtation",
)


class DecodingTrustToxicitySubset(Enum):
    """
    Which subset of the DecodingTrust toxicity prompts to load.

    ``TOXIC`` matches ``toxic.jsonl`` (1196 high-toxicity prompts; the
    red-teaming default). ``NONTOXIC`` matches ``nontoxic.jsonl`` (1200
    low-toxicity prompts). ``ALL`` fetches and concatenates both files.
    """

    TOXIC = "toxic"
    NONTOXIC = "nontoxic"
    ALL = "all"


class _DecodingTrustToxicityDataset(_RemoteDatasetLoader):
    """
    Loader for the Toxicity perspective of the DecodingTrust benchmark.

    DecodingTrust evaluates LLM trustworthiness across eight perspectives;
    the Toxicity perspective publishes ~2.4k prompts sampled from
    RealToxicityPrompts and re-scored with the Perspective API. Each record
    contains a plain-text prompt fragment, eight Perspective-API scores
    (toxicity, severe_toxicity, profanity, sexually_explicit, identity_attack,
    threat, insult, flirtation) and a ``challenging`` flag for the
    high-difficulty subset.

    The published data lives in two files at
    ``AI-secure/DecodingTrust/data/toxicity/user_prompts/`` — ``toxic.jsonl``
    (1196 records, high toxicity) and ``nontoxic.jsonl`` (1200 records, low
    toxicity). The loader fetches the source files at runtime from
    ``raw.githubusercontent.com`` (no redistribution) at a pinned commit SHA.

    Per-prompt ``harm_categories`` are derived from the Perspective scores at
    construction time using ``toxicity_threshold``: any score
    ``>= toxicity_threshold`` adds the corresponding category. This avoids
    guessing where the source provides no label.

    Reference: [@wang2023decodingtrust]
    Source: https://github.com/AI-secure/DecodingTrust

    License:
        DecodingTrust is distributed under CC BY-SA 4.0. PyRIT fetches the
        prompts at runtime and does not redistribute them.

    Warning:
        The toxic subset contains profanity, sexual content, identity attacks
        and other content that may be offensive. Use only for authorised
        red-teaming and safety research.
    """

    # Class-level metadata picked up by _RemoteDatasetLoader._parse_metadata_async.
    # See pyrit/datasets/seed_datasets/seed_metadata.py for the schema.
    # Class-level harm_categories exclude "flirtation" — Perspective API exposes
    # it as a tone/style signal rather than a harm, so it shouldn't surface
    # through dataset-discovery harm-category filters. Per-row derivation and
    # per-seed metadata still carry all 8 Perspective scores so callers can
    # threshold against `metadata["flirtation"]` if they need it.
    harm_categories: list[str] = [k for k in _PERSPECTIVE_SCORE_KEYS if k != "flirtation"]
    modalities: list[str] = ["text"]
    size: str = "large"  # 1196 (default toxic) – 2396 (subset=ALL)
    tags: set[str] = {"default", "safety", "toxicity"}

    _AUTHORS: tuple[str, ...] = (
        # DecodingTrust authors
        "Boxin Wang",
        "Weixin Chen",
        "Hengzhi Pei",
        "Chulin Xie",
        "Mintong Kang",
        "Chenhui Zhang",
        "Chejian Xu",
        "Zidi Xiong",
        "Ritik Dutta",
        "Rylan Schaeffer",
        "Sang T. Truong",
        "Simran Arora",
        "Mantas Mazeika",
        "Dan Hendrycks",
        "Zinan Lin",
        "Yu Cheng",
        "Sanmi Koyejo",
        "Dawn Song",
        "Bo Li",
    )

    _GROUPS: tuple[str, ...] = (
        "University of Illinois at Urbana-Champaign",
        "Stanford University",
        "University of California, Berkeley",
        "Center for AI Safety",
        "Microsoft Corporation",
        "The Chinese University of Hong Kong",
    )

    _DESCRIPTION = (
        "Toxicity perspective of the DecodingTrust benchmark (Wang et al., 2023). "
        "Prompts are sampled from RealToxicityPrompts and "
        "re-scored with the Perspective API. Each record carries eight Perspective "
        "scores (toxicity, severe_toxicity, profanity, sexually_explicit, "
        "identity_attack, threat, insult, flirtation) and a `challenging` flag. "
        "Per-prompt harm_categories are derived from these scores at load time."
    )

    def __init__(
        self,
        *,
        subset: DecodingTrustToxicitySubset = DecodingTrustToxicitySubset.TOXIC,
        challenging_only: bool = False,
        toxicity_threshold: float = 0.5,
    ) -> None:
        """
        Initialize the DecodingTrust Toxicity dataset loader.

        Args:
            subset: Which subset of the toxicity prompts to load. Defaults to
                ``DecodingTrustToxicitySubset.TOXIC`` — the red-teaming use case
                agreed on microsoft/PyRIT#1798. ``NONTOXIC`` loads the low-toxicity
                file; ``ALL`` concatenates both.
            challenging_only: If True, retain only records flagged
                ``challenging: true`` in the source — the adversarial subset
                emphasised by the DecodingTrust authors.
            toxicity_threshold: Perspective score at or above which a category
                is added to a SeedPrompt's ``harm_categories``. Applied
                independently to each of the eight Perspective scores. Must be
                in ``[0.0, 1.0]`` (the Perspective API score range).

        Raises:
            ValueError: If ``subset`` is not a ``DecodingTrustToxicitySubset``
                instance, or if ``toxicity_threshold`` is outside ``[0.0, 1.0]``.
        """
        self._validate_enum(subset, DecodingTrustToxicitySubset, "subset")
        if not 0.0 <= toxicity_threshold <= 1.0:
            raise ValueError(
                f"toxicity_threshold must be in [0.0, 1.0] (Perspective API score range), got {toxicity_threshold!r}"
            )
        self.subset = subset
        self.challenging_only = challenging_only
        self.toxicity_threshold = toxicity_threshold

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "decoding_trust_toxicity"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the DecodingTrust Toxicity prompts and return them as a SeedDataset.

        Args:
            cache: Whether to cache the fetched JSONL files locally. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset whose seeds are the selected toxicity prompts.

        Raises:
            ValueError: If the source JSONL contains items that are not dicts, or
                if the chosen filter combination leaves zero seeds.
        """
        logger.info(f"Loading DecodingTrust Toxicity subset={self.subset.value!r} from {_BASE_URL}")

        urls: list[str] = []
        if self.subset in (DecodingTrustToxicitySubset.TOXIC, DecodingTrustToxicitySubset.ALL):
            urls.append(_TOXIC_URL)
        if self.subset in (DecodingTrustToxicitySubset.NONTOXIC, DecodingTrustToxicitySubset.ALL):
            urls.append(_NONTOXIC_URL)

        records: list[tuple[str, dict[str, Any]]] = []
        for url in urls:
            raw = self._fetch_from_url(source=url, source_type="public_url", cache=cache)
            records.extend((url, item) for item in cast("list[Any]", raw))

        seed_prompts = self._records_to_seed_prompts(records=records)
        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")
        logger.info(f"Loaded {len(seed_prompts)} prompts from DecodingTrust Toxicity")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _records_to_seed_prompts(self, *, records: list[tuple[str, dict[str, Any]]]) -> list[SeedUnion]:
        """
        Convert raw JSONL records into SeedPrompt instances.

        Args:
            records: List of ``(source_url, record_dict)`` pairs straight from the
                JSONL files. The ``source_url`` becomes the per-prompt ``source``.

        Returns:
            List of SeedPrompt objects, one per record that passes filters and
            yields a non-empty ``prompt.text``.

        Raises:
            ValueError: If any record is not a dict (i.e. the source is malformed).
        """
        seed_prompts: list[SeedUnion] = []
        for source_url, item in records:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Expected DecodingTrust toxicity records to be JSON objects, got {type(item).__name__}: {item!r}"
                )

            if self.challenging_only and not item.get("challenging"):
                continue

            prompt_obj = item.get("prompt") or {}
            if not isinstance(prompt_obj, dict):
                logger.warning(f"Skipping record with non-dict 'prompt' field (type={type(prompt_obj).__name__})")
                continue

            text = prompt_obj.get("text")
            if not isinstance(text, str) or not text:
                logger.warning("Skipping record with missing or empty 'prompt.text'")
                continue

            seed_prompts.append(
                SeedPrompt(
                    value=text,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=self._derive_harm_categories(prompt_scores=prompt_obj),
                    description=self._DESCRIPTION,
                    source=source_url,
                    authors=list(self._AUTHORS),
                    groups=list(self._GROUPS),
                    metadata={
                        "challenging": bool(item.get("challenging", False)),
                        **{
                            key: str(prompt_obj[key])
                            for key in _PERSPECTIVE_SCORE_KEYS
                            if isinstance(prompt_obj.get(key), (int, float))
                        },
                    },
                )
            )
        return seed_prompts

    def _derive_harm_categories(self, *, prompt_scores: dict[str, Any]) -> list[str]:
        """
        Map Perspective API scores on a single prompt to a list of harm categories.

        Each Perspective score whose value is ``>= self.toxicity_threshold``
        contributes its key as a harm category. Non-numeric or missing scores
        are silently skipped — the source occasionally omits dimensions.

        Args:
            prompt_scores: The ``prompt`` sub-dict from a DecodingTrust toxicity
                record, containing the eight Perspective scores plus ``text``.

        Returns:
            Sorted list of harm category strings. Sorted for deterministic
            output (tests, snapshots).
        """
        categories: list[str] = []
        for key in _PERSPECTIVE_SCORE_KEYS:
            score = prompt_scores.get(key)
            if isinstance(score, (int, float)) and score >= self.toxicity_threshold:
                categories.append(key)
        categories.sort()
        return categories
