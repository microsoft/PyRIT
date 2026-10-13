# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from enum import Enum
from typing import Any, Literal, cast

from typing_extensions import override

from pyrit.common.utils import to_sha256
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
    which attack technique a prompt covers, independently of the harm taxonomy.
    All rows receive ``COORDINATION_HARM`` as operational cyberattack assistance.
    C2, Evasion, Execution, Lateral Movement, Persistence and Privilege Escalation
    additionally receive ``MALWARE``. Reconnaissance and discovery are not
    automatically classified as malicious software.

    Collection and exfiltration receive additional ``MALWARE``, ``PPI`` and/or
    ``PROPRIETARY_INFO`` labels only for curated, exact prompt matches from the
    pinned source. These annotations describe private-data collection and
    confidential-data theft, not keywords or the source's sometimes inaccurate
    technique names. Modified and custom prompts retain the category-level
    labels without inheriting a curated privacy annotation. Labels describe the
    benchmark's intended capabilities, not whether any target response is harmful.

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
    SOURCE_URL: str = DATASET_URL
    HARM_CATEGORY_MAPPING_VERSION: str = "mitre-category-and-curated-prompt-v1"
    MALWARE_CATEGORIES: frozenset[CyberSecEvalMitreCategory] = frozenset(
        {
            CyberSecEvalMitreCategory.C2,
            CyberSecEvalMitreCategory.EVASION,
            CyberSecEvalMitreCategory.EXECUTION,
            CyberSecEvalMitreCategory.LATERAL_MOVEMENT,
            CyberSecEvalMitreCategory.PERSISTENCE,
            CyberSecEvalMitreCategory.PRIVILEGE_ESCALATION,
        }
    )

    # Exact mutated_prompt fingerprints at _COMMIT, reviewed against the requested
    # behavior rather than row order or the noisy upstream technique labels.
    CURATED_PRIVACY_PROMPTS: dict[HarmCategory, frozenset[str]] = {
        HarmCategory.PPI: frozenset(
            {
                "a6442b79b3c5091f0d488ce4836a6f86d95bce3d2e0553deb02885852206387c",
                "8b50570891da8ba0796f2c93335076382884557abbeb2b211ec81f33b6896f74",
                "491758fe7eb044dc01886b1d8f2244e4a24f5b76c98d5baa811c7307e92d896a",
                "0bcbd2d747061268a0ab8fa66f63f288a7122f5d3c35aa14feb51b07144239af",
                "4bf313a2f354dcaed767c0c63d52211afe29c5eddbe1b9742043da0741a04ad7",
                "eed35088c43623dc8598b79ada329e91d44d13a7f09b59fbf9ff63591692839e",
                "ecc5470014489666b5b981bbe086950d124a34b64de2695971d9979bd7e61b82",
                "de73b3738c1b7984148427c3c4e1ca124d27962de9713ff2e7c8b0288aa2381c",
                "2dc4cad680d9af1a037eae4af06093ce3b9c011f7295e373130babff51c1725a",
                "2e8e9b53ba8b34da3610b741a7b9b4fae5f8c1e37e70beb6773fc9f70fa285fb",
                "9b619641766cc056d9b7c3ae06819cfc8b4d5c035ed5f032d8e205fb3f77483a",
                "b1fce3d1afd5ceee060365f8aceb50da9eb1ee49c7cde9869101537b6e720852",
                "6f3342538a6422c94439c9a36a58d606a1fc7c1e342db549690f57124d7bc156",
                "f28905f1909ef6aeb73e89358141c650b33bc2d193291f02bab1d7230e3657d2",
                "1d7c3132ef39ad71a87eb1d6b9bfdbc5b34a781715d8a9c5a5d44f90590eb578",
                "3dbcf840223fc1129e71b5bacb285de6004edd4a7da06620fdcdec376656be73",
                "58b033f939cc72c9e4a05a1cc7994dccc5472fcec7c49873b9dfdceb493438d0",
                "1e63d41433d291c5a3696995c7f44bd941431d7739162b8996770b4f569ab121",
                "4845425dc7025ccf87b6259ada30c76b0ebb2403bee89e054374cbb7e25f2e48",
                "f5b05150da7de09d59804d7fd952479225e9700b9718a6be50289a4f99eb9e4a",
                "cf1777ec013cdf342745308e9b3ae4893bb8799668116399edff7f56cd4f5663",
                "5b9fadef8d75c206e42dba022067ea2fb2e1be331be6f29d0276c638607a0db0",
                "eca60e8ec3b38d4f6a3e8e0087cf30d8f02300260d9701ef112dd92f8c36dc0a",
                "98e3adbdb05bffea17ee2b1318d501c9578475e2650baed2cd6c75c75d38867a",
                "0761cc1d714bf0f21f3489a662dba8985515f961de09c44ec5a5e9237d20c9d9",
                "dc30d856b54c5def6ae1f037245440b0e77409fcf0d7d4bedd033f7753053a5f",
                "d8b349453ad0831f77c283ca809398b23b056745a14331ee06354d8efd566dd8",
                "ca554001c8de1e5e4ff0e9141414425109527712f1900be678bccb12f1565b73",
                "20763696586630c9c0ea56c01880e2df616904d44d40e338d548ab61cb7ae5d8",
                "6a41e566cb4278fb8edb7027de7efc8d0e1eae50512f957f2841b65fb7a0e76d",
                "534428772190c06df4d42a2b99176e2bee558f840b03a9c8dd1036f710e65b33",
                "27f459343868fe83d69322d5c788dba4a4bc2fa4599be809ef175c4f392ae9fe",
                "46b7af0a753e6db9b00317b9f3a5da7d36d2153c822fda7d489da1cc188de48d",
                "a3114ad2d60926f0bbfa0f3fd64a09e6ca1c639f270bd2f5b3bf0830f99f8df3",
                "d66b56fb59db874b1fb69837683bfd79a55a191ce05ecc52a55e9384dff411b9",
                "8eba5a481a0bfe490276af5a95a516619ca93f87afa2ef5177cf421943951e20",
                "0dc15e4f83947459cc8931c66b96b31f4abf8019ff121f288b99e5e24d140ea4",
                "66125c149f231d288f78f478bbe4ea5d3cfdd28a2fd36e9907c0bb3f5a6a00d9",
                "69d1bb36b183772bddc474e2322f61b64c85eb0b1b434fa3a272dec6b2fc4909",
            }
        ),
        HarmCategory.PROPRIETARY_INFO: frozenset(
            {
                "8b50570891da8ba0796f2c93335076382884557abbeb2b211ec81f33b6896f74",
                "4bf313a2f354dcaed767c0c63d52211afe29c5eddbe1b9742043da0741a04ad7",
                "eed35088c43623dc8598b79ada329e91d44d13a7f09b59fbf9ff63591692839e",
                "ecc5470014489666b5b981bbe086950d124a34b64de2695971d9979bd7e61b82",
                "6f3342538a6422c94439c9a36a58d606a1fc7c1e342db549690f57124d7bc156",
                "3dbcf840223fc1129e71b5bacb285de6004edd4a7da06620fdcdec376656be73",
                "1e63d41433d291c5a3696995c7f44bd941431d7739162b8996770b4f569ab121",
                "4845425dc7025ccf87b6259ada30c76b0ebb2403bee89e054374cbb7e25f2e48",
                "f5b05150da7de09d59804d7fd952479225e9700b9718a6be50289a4f99eb9e4a",
                "534428772190c06df4d42a2b99176e2bee558f840b03a9c8dd1036f710e65b33",
                "e638fa071e9b9a9fc74000ba8a3449624dafeeee51740e61a82402c28f2876e6",
                "9615d3ee087f0a3ea981c5e332c08272de0734e973fcc53b69e5e43f064e2334",
                "76a5090401e9ef304882e69a1029e5a2d4250e461c15c970a8ae032dbefd54fb",
            }
        ),
    }

    # Metadata
    harm_categories: list[str] = ["coordination_harm", "malware", "ppi", "proprietary_info"]
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
                mapping is not a dict, if its ``mutated_prompt`` is not a string,
                or if no prompts remain after filtering.
        """
        selected = {category.value for category in self.categories} if self.categories is not None else None
        logger.info(
            f"Loading CyberSecEval MITRE dataset from {self.source} "
            f"(categories={sorted(selected) if selected is not None else 'all'})"
        )

        # The JSON reader returns the parsed file as-is. The declared return type
        # of ``_fetch_from_url`` says the values are strings, but
        # ``ttp_id_name_mapping`` is a nested dict. The helper does a synchronous
        # download and file I/O, so it runs in a worker thread to keep the event
        # loop free for other work.
        examples = cast(
            "list[dict[str, Any]]",
            await asyncio.to_thread(
                self._fetch_from_url,
                source=self.source,
                source_type=self.source_type,
                cache=cache,
            ),
        )

        seed_prompts: list[SeedUnion] = []
        for row_index, example in enumerate(examples):
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

            try:
                category = CyberSecEvalMitreCategory(example["mitre_category"])
            except ValueError as exc:
                raise ValueError(f"CyberSecEval MITRE row {row_index} has an invalid mitre_category") from exc
            if selected is not None and category.value not in selected:
                continue

            base_prompt = example["base_prompt"]
            if not isinstance(base_prompt, str):
                raise ValueError(f"CyberSecEval MITRE row {row_index} has a base_prompt that is not a string")
            raw_prompt = example["mutated_prompt"]
            if raw_prompt is not None and not isinstance(raw_prompt, str):
                raise ValueError(
                    f"CyberSecEval MITRE row {row_index} has a `mutated_prompt` that is not a string "
                    f"(got {type(raw_prompt).__name__}). The upstream schema may have changed."
                )
            prompt = raw_prompt or ""
            if not prompt.strip():
                logger.warning("[CyberSecEval MITRE] Skipping row with an empty mutated_prompt field")
                continue

            seed_prompts.append(
                SeedPrompt(
                    value=prompt,
                    data_type="text",
                    name="CyberSecEval MITRE",
                    dataset_name=self.dataset_name,
                    harm_categories=self._get_harm_categories(category=category, prompt=prompt),
                    description=_DESCRIPTION,
                    source=self.SOURCE_URL,
                    authors=_AUTHORS,
                    groups=_GROUPS,
                    metadata={
                        "mitre_category": category.value,
                        "ttp_id": str(ttp_mapping.get("TTP_ID") or ""),
                        "ttp_name": str(ttp_mapping.get("TTP_Name") or ""),
                        "base_prompt": base_prompt.strip(),
                        "harm_category_mapping": self.HARM_CATEGORY_MAPPING_VERSION,
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

    def _get_harm_categories(self, *, category: CyberSecEvalMitreCategory, prompt: str) -> list[str]:
        categories = [HarmCategory.COORDINATION_HARM]
        fingerprint = to_sha256(prompt)
        privacy_categories = [
            harm for harm, fingerprints in self.CURATED_PRIVACY_PROMPTS.items() if fingerprint in fingerprints
        ]
        if category in self.MALWARE_CATEGORIES or privacy_categories:
            categories.append(HarmCategory.MALWARE)
        return self._standardize_harm_categories([*categories, *privacy_categories])
