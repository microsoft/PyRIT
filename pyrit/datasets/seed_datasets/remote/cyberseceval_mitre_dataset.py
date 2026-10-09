# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Any, Literal, cast

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class CyberSecEvalMitreCategory(Enum):
    """
    MITRE ATT&CK categories covered by the CyberSecEval MITRE benchmark.

    Values are the verbatim strings stored in the upstream ``mitre_category``
    field. The benchmark has 100 prompts for each of the ten categories.
    """

    C2 = "C2"
    COLLECTION = "Collection"
    DISCOVERY = "Discovery"
    EVASION = "Evasion"
    EXECUTION = "Execution"
    EXFIL = "Exfil"
    LATERAL_MOVEMENT = "Lateral Movement"
    PERSISTENCE = "Persistence"
    PRIVILEGE_ESCALATION = "Privilege Escalation"
    RECON = "Recon"


_AUTHORS: list[str] = [
    "Manish Bhatt",
    "Sahana Chennabasappa",
    "Cyrus Nikolaidis",
    "Shengye Wan",
    "Ivan Evtimov",
    "Dominik Gabi",
    "Daniel Song",
    "Faizan Ahmad",
    "Cornelius Aschermann",
    "Lorenzo Fontana",
    "Sasha Frolov",
    "Ravi Prakash Giri",
    "Dhaval Kapil",
    "Yiannis Kozyrakis",
    "David LeBlanc",
    "James Milazzo",
    "Aleksandar Straumann",
    "Gabriel Synnaeve",
    "Varun Vontimitta",
    "Spencer Whitman",
    "Joshua Saxe",
]

_GROUPS: list[str] = ["Meta"]

_REQUIRED_KEYS: frozenset[str] = frozenset({"base_prompt", "mutated_prompt", "mitre_category", "ttp_id_name_mapping"})

_DESCRIPTION: str = (
    "CyberSecEval MITRE benchmark prompt. Each prompt asks the model to write code for an offensive "
    "cyber operation that maps to a MITRE ATT&CK technique. The benchmark measures how readily a model "
    "complies with requests to help with cyberattacks. Introduced in 'Purple Llama CyberSecEval: A Secure "
    "Coding Benchmark for Language Models' (Bhatt et al., 2023)."
)


class _CyberSecEvalMitreDataset(_RemoteDatasetLoader):
    """
    Loader for the MITRE benchmark of Meta's CyberSecEval (Purple Llama).

    The MITRE benchmark tests how readily a model helps with cyberattacks. It
    contains 1,000 prompts, 100 for each of ten MITRE ATT&CK categories, and
    every prompt is labelled with the ATT&CK technique it maps to.

    Each upstream row has a short ``base_prompt`` and a longer ``mutated_prompt``
    that an LLM expanded from it. CyberSecEval sends ``mutated_prompt`` to the
    model under test without changes, so this loader emits it verbatim as the
    ``SeedPrompt`` value. That includes the JSON-style wrapper around the text
    and the trailing "Your response implementing the above in code follows:".
    ``base_prompt`` is kept in ``metadata``.

    The ATT&CK category, technique ID and technique name are stored in
    ``metadata`` as ``mitre_category``, ``ttp_id`` and ``ttp_name``. They describe
    which attack technique a prompt covers. They are not content-harm labels, so
    every seed gets the same ``harm_categories`` value (``MALWARE``).

    This loader covers the English prompts only. CyberSecEval's expansion and
    judge steps, its false refusal rate prompts and its machine-translated
    multilingual prompts are out of scope.

    References:
        - https://github.com/meta-llama/PurpleLlama/tree/main/CybersecurityBenchmarks
        - [@bhatt2023cyberseceval]

    License: MIT (CyberSecEval benchmarks in the Purple Llama repository).

    Warning: This dataset contains requests for offensive cyber capabilities
    such as malware, command-and-control, evasion and exfiltration code.
    """

    _COMMIT: str = "172c1074069eb88ec834124272c1b1c4f8893445"
    DATASET_URL: str = (
        f"https://raw.githubusercontent.com/meta-llama/PurpleLlama/{_COMMIT}/"
        "CybersecurityBenchmarks/datasets/mitre/mitre_benchmark_100_per_category_with_augmentation.json"
    )
    SOURCE_URL: str = "https://github.com/meta-llama/PurpleLlama/tree/main/CybersecurityBenchmarks"

    # Metadata
    harm_categories: list[str] = ["malware"]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 1,000 prompts
    tags: set[str] = {"safety", "cybersecurity"}

    def __init__(
        self,
        *,
        source: str = DATASET_URL,
        source_type: Literal["public_url", "file"] = "public_url",
        categories: list[CyberSecEvalMitreCategory] | None = None,
    ) -> None:
        """
        Initialize the CyberSecEval MITRE dataset loader.

        Args:
            source: URL or local path to the MITRE benchmark JSON file. Defaults
                to a pinned commit of the Purple Llama repository so that a
                default fetch is reproducible.
            source_type: ``"public_url"`` or ``"file"``.
            categories: List of CyberSecEvalMitreCategory values to filter by.
                Defaults to None (all ten categories).

        Raises:
            ValueError: If ``categories`` is an empty list, or contains a value
                that is not a ``CyberSecEvalMitreCategory``.
        """
        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(categories, CyberSecEvalMitreCategory, "category")

        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        # Copied, so a caller mutating its own list afterwards cannot change
        # what this loader filters on.
        self.categories = list(categories) if categories is not None else None

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "cyberseceval_mitre"

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the CyberSecEval MITRE benchmark and return it as a SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset with one SeedPrompt per benchmark prompt,
            filtered by ``self.categories``.

        Raises:
            ValueError: If a row is missing an expected key, if its technique
                mapping is not a dict, or if no prompts remain after filtering.
        """
        selected = {category.value for category in self.categories} if self.categories is not None else None
        logger.info(
            f"Loading CyberSecEval MITRE dataset from {self.source} "
            f"(categories={sorted(selected) if selected is not None else 'all'})"
        )

        # The JSON reader returns the parsed file as-is. The declared return type
        # of ``_fetch_from_url`` says the values are strings, but
        # ``ttp_id_name_mapping`` is a nested dict.
        examples = cast(
            "list[dict[str, Any]]",
            self._fetch_from_url(source=self.source, source_type=self.source_type, cache=cache),
        )

        harm_categories = self._standardize_harm_categories([HarmCategory.MALWARE])

        seed_prompts: list[SeedUnion] = []
        for example in examples:
            missing_keys = _REQUIRED_KEYS - example.keys()
            if missing_keys:
                raise ValueError(
                    f"CyberSecEval MITRE row is missing expected key(s) {sorted(missing_keys)}; "
                    f"row has {sorted(example.keys())}. The upstream schema may have changed."
                )

            ttp_mapping = example["ttp_id_name_mapping"]
            if not isinstance(ttp_mapping, dict):
                raise ValueError(
                    "CyberSecEval MITRE row has a `ttp_id_name_mapping` that is not a dict "
                    f"(got {type(ttp_mapping).__name__}). The upstream schema may have changed."
                )

            mitre_category = str(example["mitre_category"] or "").strip()
            if selected is not None and mitre_category not in selected:
                continue

            prompt = str(example["mutated_prompt"] or "")
            if not prompt.strip():
                logger.warning("[CyberSecEval MITRE] Skipping row with an empty mutated_prompt field")
                continue

            seed_prompts.append(
                SeedPrompt(
                    value=prompt,
                    data_type="text",
                    name="CyberSecEval MITRE",
                    dataset_name=self.dataset_name,
                    harm_categories=harm_categories,
                    description=_DESCRIPTION,
                    source=self.SOURCE_URL,
                    authors=_AUTHORS,
                    groups=_GROUPS,
                    metadata={
                        "mitre_category": mitre_category,
                        "ttp_id": str(ttp_mapping.get("TTP_ID") or ""),
                        "ttp_name": str(ttp_mapping.get("TTP_Name") or ""),
                        "base_prompt": str(example["base_prompt"] or "").strip(),
                    },
                )
            )

        if not seed_prompts:
            raise ValueError(
                "SeedDataset cannot be empty. Check your filter criteria. "
                f"CyberSecEval MITRE filter: categories={sorted(selected) if selected is not None else '(any)'}."
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from CyberSecEval MITRE dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
