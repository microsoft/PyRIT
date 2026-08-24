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

HF_DATASET_NAME: str = "ScaleAI/fortress_public"
HF_REVISION: str = "0c096becbc75bb12065c8059a53960c7f0d4d35c"
HF_SOURCE_URL: str = f"https://huggingface.co/datasets/{HF_DATASET_NAME}"

# The risk_domain field in HF stores CBRNE's full descriptive name. Other two
# domains are stored verbatim as below. Inspect Evals applies the same expansion.
_CBRNE_FULL: str = "Chemical, Biological, Radiological, Nuclear, and Explosive (CBRNE)"


class FortressSplit(Enum):
    """
    Which half of the FORTRESS dataset to load.

    Every upstream row carries both an adversarial prompt and a benign rephrasing
    on the same topic. This enum selects which half(s) the loader emits.

    - ``ADVERSARIAL``: the 500 expert-crafted adversarial prompts (default).
    - ``BENIGN``: the 500 benign rephrasings, for over-refusal measurement.
    - ``ALL``: both halves, 1000 seeds total (adversarial then benign per row).
    """

    ADVERSARIAL = "adversarial"
    BENIGN = "benign"
    ALL = "all"


class FortressRiskDomain(Enum):
    """
    Filter for which Fortress risk domain to load.

    Values match the canonical short labels from the upstream paper. CBRNE is stored
    in the HF dataset under its long descriptive name and is expanded internally
    when filtering, so users always pass the short ``"CBRNE"`` form.
    """

    CBRNE = "CBRNE"
    POLITICAL_VIOLENCE_AND_TERRORISM = "Political Violence & Terrorism"
    CRIMINAL_AND_FINANCIAL_ILLICIT_ACTIVITIES = "Criminal & Financial Illicit Activities"


class FortressRiskSubdomain(Enum):
    """
    Filter for which Fortress risk subdomain to load.

    Values are the short title prefix used by the upstream dataset; the stored
    ``risk_subdomain`` field carries a longer descriptive suffix after a colon
    (e.g. ``"Illegal Weapons: 3d-printed weapons, ..."``). Filtering uses a
    prefix match against this short form so the descriptive suffix is preserved
    in metadata for downstream consumers.

    Note: the upstream README documents that some rows have a subdomain
    assignment that does not match the canonical domain mapping
    (e.g. a ``Terrorism`` subdomain row under the ``CBRNE`` domain). Filters
    respect the stored values, not the documented mapping, so such rows are
    surfaced exactly as upstream emits them.
    """

    CHEMICAL = "Chemical"
    BIOLOGICAL = "Biological"
    RADIOLOGICAL_NUCLEAR_WMD = "Radiological and Nuclear (WMD)"
    EXPLOSIVES = "Explosives"
    TERRORISM = "Terrorism"
    POLITICAL_VIOLENCE = "Political Violence"
    ILLEGAL_WEAPONS = "Illegal Weapons"
    COORDINATION_OF_ILLICIT_ACTIVITIES = "Coordination of Illicit Activities"
    FRAUD = "Fraud"
    PRIVACY_SCAMS = "Privacy/Scams"


_AUTHORS: list[str] = [
    "Christina Q. Knight",
    "Kaustubh Deshpande",
    "Ved Sirdeshmukh",
    "Meher Mankikar",
    "Scale Red Team",
    "SEAL Research Team",
    "Julian Michael",
]

_GROUPS: list[str] = ["Scale AI"]


class _FortressDataset(_RemoteDatasetLoader):
    """
    Loader for the FORTRESS dataset from Scale AI (national-security safety eval).

    FORTRESS is 500 expert-crafted adversarial prompts, each paired with a benign
    rephrasing on the same topic and an instance-specific 4-7 binary-question rubric.
    Designed for the paired (ARS, ORS) safety-vs-utility evaluation in the paper.

    ``split`` selects which half(s) to emit (default ``FortressSplit.ADVERSARIAL``;
    pass ``FortressSplit.ALL`` for both halves, 1000 seeds total).

    Use restriction: This dataset should be used for safety evaluations only, and
    it is prohibited to use these data for any adversarial training or research.

    References:
        - Paper: [@knight2025fortress] (arXiv:2506.14922).
        - HuggingFace: https://huggingface.co/datasets/ScaleAI/fortress_public
        - Project page: https://scale.com/research/fortress
        - Inspect Evals port (canonical execution harness):
          https://github.com/UKGovernmentBEIS/inspect_evals/tree/main/src/inspect_evals/fortress

    License: CC BY 4.0 with the Scale AI use restriction quoted above.
    """

    HF_DATASET_NAME: str = HF_DATASET_NAME
    HF_REVISION: str = HF_REVISION

    modalities: list[str] = ["text"]
    size: str = "medium"
    tags: set[str] = {"safety", "jailbreak", "refusal", "calibration", "national_security"}
    harm_categories: list[str] = [
        "biological",
        "chemical",
        "coordination of illicit activities",
        "explosives",
        "fraud",
        "illegal weapons",
        "political violence",
        "privacy/scams",
        "radiological and nuclear (wmd)",
        "terrorism",
    ]

    _DESCRIPTION: str = (
        "FORTRESS: 500 expert-crafted national-security adversarial prompts (CBRNE, "
        "Political Violence & Terrorism, Criminal & Financial Illicit Activities), each "
        "paired with a benign rephrasing on the same topic. Adversarial seeds carry a "
        "per-row 4-7-question binary rubric in metadata for use with FortressRubricScorer; "
        "both halves carry the partner prompt for (ARS, ORS) trade-off analysis."
    )

    def __init__(
        self,
        *,
        split: FortressSplit = FortressSplit.ADVERSARIAL,
        risk_domain: FortressRiskDomain | None = None,
        risk_subdomain: FortressRiskSubdomain | None = None,
    ) -> None:
        """
        Initialize the Fortress dataset loader.

        Args:
            split (FortressSplit): Which half(s) to load. Defaults to ``ADVERSARIAL``.
            risk_domain (FortressRiskDomain | None): If set, only return rows whose
                ``risk_domain`` field matches this domain. Defaults to None (all domains).
            risk_subdomain (FortressRiskSubdomain | None): If set, only return rows whose
                ``risk_subdomain`` field starts with the given subdomain prefix.
                Defaults to None (all subdomains).

        Raises:
            ValueError: If ``split`` / ``risk_domain`` / ``risk_subdomain`` is not the expected enum type.
        """
        self._validate_enum(value=split, enum_cls=FortressSplit, label="split")
        if risk_domain is not None:
            self._validate_enum(value=risk_domain, enum_cls=FortressRiskDomain, label="risk_domain")
        if risk_subdomain is not None:
            self._validate_enum(value=risk_subdomain, enum_cls=FortressRiskSubdomain, label="risk_subdomain")

        self.split = split
        self.risk_domain = risk_domain
        self.risk_subdomain = risk_subdomain

    async def _fetch_filtered_rows_async(self, *, cache: bool) -> list[dict[str, Any]]:
        """
        Fetch the (single) Fortress train split from HF and apply enum filters.

        Args:
            cache (bool): Whether to cache the fetched dataset.

        Returns:
            list[dict[str, Any]]: The filtered raw HF rows in upstream order.

        Raises:
            ValueError: If no rows remain after filtering.
        """
        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split="train",
            cache=cache,
            revision=self.HF_REVISION,
        )

        domain_filter = self._resolved_domain_filter_value()
        subdomain_prefix = self.risk_subdomain.value if self.risk_subdomain is not None else None

        filtered: list[dict[str, Any]] = []
        for row in data:
            if domain_filter is not None and row.get("risk_domain") != domain_filter:
                continue
            if subdomain_prefix is not None and not str(row.get("risk_subdomain", "")).startswith(subdomain_prefix):
                continue
            filtered.append(dict(row))

        if not filtered:
            domain_label = self.risk_domain.value if self.risk_domain is not None else "(any)"
            subdomain_label = self.risk_subdomain.value if self.risk_subdomain is not None else "(any)"
            raise ValueError(
                "SeedDataset cannot be empty. Check your filter criteria. "
                f"Fortress filter: risk_domain={domain_label!r}, risk_subdomain={subdomain_label!r}."
            )

        return filtered

    def _resolved_domain_filter_value(self) -> str | None:
        """
        Resolve the value used to filter the HF ``risk_domain`` column.

        CBRNE is stored under its long descriptive form in the HF data, so the
        short enum value must be expanded before comparison. The other two
        domains use their enum value verbatim.

        Returns:
            str | None: The string to match against ``row["risk_domain"]``, or
                None if no domain filter is set.
        """
        if self.risk_domain is None:
            return None
        if self.risk_domain == FortressRiskDomain.CBRNE:
            return _CBRNE_FULL
        return self.risk_domain.value

    def _build_seed_prompt(
        self,
        *,
        row: dict[str, Any],
        adversarial_or_benign: str,
    ) -> SeedPrompt:
        """
        Build a single SeedPrompt for either the adversarial or benign half of a row.

        Args:
            row (dict[str, Any]): A raw Fortress HF row.
            adversarial_or_benign (str): ``"adversarial"`` or ``"benign"``.

        Returns:
            SeedPrompt: The constructed seed prompt.

        Raises:
            ValueError: If ``adversarial_or_benign`` is not one of the two valid values.
        """
        if adversarial_or_benign not in ("adversarial", "benign"):
            raise ValueError(f"adversarial_or_benign must be 'adversarial' or 'benign'; got {adversarial_or_benign!r}.")

        is_adversarial = adversarial_or_benign == "adversarial"
        value = row["adversarial_prompt"] if is_adversarial else row["benign_prompt"]
        paired_value = row["benign_prompt"] if is_adversarial else row["adversarial_prompt"]
        subdomain_full = str(row.get("risk_subdomain", ""))
        subdomain_short = _shorten_risk_subdomain(subdomain_full)

        metadata: dict[str, str | int] = {
            "fortress_id": int(row["ID"]),
            "risk_domain": str(row.get("risk_domain", "")),
            "risk_subdomain": subdomain_short,
            "risk_subdomain_full": subdomain_full,
            "adversarial_or_benign": adversarial_or_benign,
            "paired_prompt": str(paired_value),
            "use_restriction": "no_adversarial_training",
        }

        if is_adversarial:
            rubric_list = list(row.get("rubric") or [])
            metadata["rubric"] = "\n".join(str(c) for c in rubric_list)
            metadata["num_dim"] = len(rubric_list)
            metadata["original_prompt"] = str(value)

        return SeedPrompt(
            value=str(value),
            data_type="text",
            dataset_name=self.dataset_name,
            harm_categories=[subdomain_short] if subdomain_short else [],
            description=self._DESCRIPTION,
            source=HF_SOURCE_URL,
            authors=_AUTHORS,
            groups=_GROUPS,
            metadata=metadata,
        )

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "fortress"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the Fortress dataset as a SeedDataset, respecting ``self.split``.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: The selected Fortress prompts.

        Raises:
            ValueError: If no rows remain after filtering.
        """
        logger.info(
            f"Loading Fortress dataset (split={self.split.value}, "
            f"risk_domain={self.risk_domain}, risk_subdomain={self.risk_subdomain})"
        )

        rows = await self._fetch_filtered_rows_async(cache=cache)
        emit_adv = self.split in (FortressSplit.ADVERSARIAL, FortressSplit.ALL)
        emit_benign = self.split in (FortressSplit.BENIGN, FortressSplit.ALL)

        seeds: list[SeedPrompt] = []
        for row in rows:
            if emit_adv:
                seeds.append(self._build_seed_prompt(row=row, adversarial_or_benign="adversarial"))
            if emit_benign:
                seeds.append(self._build_seed_prompt(row=row, adversarial_or_benign="benign"))

        logger.info(f"Loaded {len(seeds)} prompts from Fortress dataset (split={self.split.value})")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)


def _shorten_risk_subdomain(long_subdomain: str) -> str:
    """
    Shorten the stored ``risk_subdomain`` to its canonical title prefix.

    HF stores the subdomain as ``"<Title>: <descriptive suffix>"``. We match against
    the known short prefixes from ``FortressRiskSubdomain`` and return the
    matching short form. If no match (unexpected upstream value), the raw value
    before the first colon is returned as a best-effort fallback.

    Args:
        long_subdomain (str): The stored ``risk_subdomain`` value.

    Returns:
        str: The short title form (e.g. ``"Illegal Weapons"``), or an empty string
            when the input is empty.
    """
    if not long_subdomain:
        return ""
    for subdomain in FortressRiskSubdomain:
        if long_subdomain.startswith(subdomain.value):
            return subdomain.value
    # Best-effort fallback for unexpected upstream values.
    return long_subdomain.split(":", 1)[0].strip()
