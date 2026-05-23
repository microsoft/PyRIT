# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Loaders for the XL-SafetyBench Jailbreak and Cultural benchmarks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


_HF_REPO_ID = "AIM-Intelligence/XL-SafetyBench"
_HF_DATASET_URL = f"https://huggingface.co/datasets/{_HF_REPO_ID}"
_HF_RESOLVE_BASE = f"{_HF_DATASET_URL}/resolve/main"
_PAPER_URL = "https://arxiv.org/abs/2605.05662"

_AUTHORS: list[str] = [
    "Dasol Choi",
    "Eugenia Kim",
    "Jaewon Noh",
    "Sang Seo",
    "Eunmi Kim",
    "Myunggyo Oh",
    "Yunjin Park",
    "Brigitta Jesica Kartono",
    "Josef Pichlmeier",
    "Helena Berndt",
    "Sai Krishna Mendu",
    "Glenn Johannes Tungka",
    "Özlem Gökçe",
    "Suresh Gehlot",
    "Katherine Pratt",
    "Amanda Minnich",
    "Haon Park",
]

_GROUPS: list[str] = [
    "AIM Intelligence",
    "Microsoft",
    "Korea AI Safety Institute",
    "KT Corporation",
]


class XLSafetyBenchCountry(str, Enum):
    """Country-language pairs covered by XL-SafetyBench."""

    FRANCE = "france"
    GERMANY = "germany"
    INDIA = "india"
    INDONESIA = "indonesia"
    JAPAN = "japan"
    SOUTH_KOREA = "south_korea"
    SPAIN = "spain"
    TURKEY = "turkey"
    UNITED_ARAB_EMIRATES = "united_arab_emirates"
    UNITED_STATES = "united_states"


class XLSafetyBenchJailbreakCategory(str, Enum):
    """Harm categories used by the XL-SafetyBench Jailbreak track."""

    CRIMINAL_ACTIVITIES = "Criminal Activities"
    SELF_HARM_AND_DANGEROUS_ADVICE = "Self-harm & Dangerous Advice"
    HATE_AND_DISCRIMINATION = "Hate & Discrimination"
    SOCIOECONOMIC_CONFLICTS = "Socioeconomic Conflicts"
    POLITICAL_AND_MISINFORMATION = "Political & Misinformation"


class XLSafetyBenchCulturalCategory(str, Enum):
    """Cultural categories used by the XL-SafetyBench Cultural track."""

    SYMBOLIC_TABOOS_AND_GIFT_GIVING = "Symbolic Taboos & Gift-Giving"
    FOOD_DIETARY_LAW_AND_HOSPITALITY = "Food Dietary Law & Hospitality"
    DEATH_GRIEF_AND_FUNERAL_PRACTICES = "Death Grief & Funeral Practices"
    DAILY_LIFE_AND_PUBLIC_CONDUCT = "Daily Life & Public Conduct"
    HIERARCHY_ADDRESS_AND_SOCIAL_DEFERENCE = "Hierarchy Address & Social Deference"
    LEGAL_LANDMINES = "Legal Landmines"


@dataclass(frozen=True)
class _CountryInfo:
    """Display and language metadata for an XL-SafetyBench country."""

    iso_639_1_code: str
    language_display_name: str
    country_display_name: str


# Country → display name + language metadata (country display names mirror the paper and are used
# in judge prompts at score time).
_COUNTRY_INFO: dict[XLSafetyBenchCountry, _CountryInfo] = {
    XLSafetyBenchCountry.FRANCE: _CountryInfo("fr", "French", "France"),
    XLSafetyBenchCountry.GERMANY: _CountryInfo("de", "German", "Germany"),
    XLSafetyBenchCountry.INDIA: _CountryInfo("hi", "Hindi", "India"),
    XLSafetyBenchCountry.INDONESIA: _CountryInfo("id", "Indonesian", "Indonesia"),
    XLSafetyBenchCountry.JAPAN: _CountryInfo("ja", "Japanese", "Japan"),
    XLSafetyBenchCountry.SOUTH_KOREA: _CountryInfo("ko", "Korean", "South Korea"),
    XLSafetyBenchCountry.SPAIN: _CountryInfo("es", "Spanish", "Spain"),
    XLSafetyBenchCountry.TURKEY: _CountryInfo("tr", "Turkish", "Turkey"),
    XLSafetyBenchCountry.UNITED_ARAB_EMIRATES: _CountryInfo("ar", "Arabic", "United Arab Emirates"),
    XLSafetyBenchCountry.UNITED_STATES: _CountryInfo("en", "English", "United States"),
}


def _resolve_countries(countries: Optional[list[XLSafetyBenchCountry]]) -> list[XLSafetyBenchCountry]:
    """
    Validate and normalize the requested list of country filters.

    Args:
        countries (Optional[list[XLSafetyBenchCountry]]): User-supplied countries, or ``None``
            to include every country.

    Returns:
        list[XLSafetyBenchCountry]: A non-empty list of countries to include (duplicates removed,
            original order preserved).

    Raises:
        ValueError: If ``countries`` is an empty list or contains non-enum values.
    """
    if countries is None:
        return list(XLSafetyBenchCountry)

    if not countries:
        raise ValueError(
            "countries must not be an empty list. Pass None to include every country, "
            "or pass at least one XLSafetyBenchCountry value."
        )

    _RemoteDatasetLoader._validate_enums(countries, XLSafetyBenchCountry, "country")

    seen: set[XLSafetyBenchCountry] = set()
    deduped: list[XLSafetyBenchCountry] = []
    for country in countries:
        if country not in seen:
            seen.add(country)
            deduped.append(country)
    return deduped


def _resolve_category_filter(
    *,
    categories: Optional[Sequence[Enum]],
    enum_cls: type[Enum],
    label: str,
) -> Optional[set[str]]:
    """
    Validate a category filter and return the set of allowed category strings.

    Args:
        categories (Optional[Sequence[Enum]]): User-supplied list of category enum members,
            or ``None`` to include every category.
        enum_cls (type[Enum]): The expected enum class.
        label (str): Human-readable label used in error messages (e.g. ``"category"``).

    Returns:
        Optional[set[str]]: A set of allowed category string values, or ``None`` when
            every category is allowed.

    Raises:
        ValueError: If ``categories`` is an empty list or contains non-enum values.
    """
    if categories is None:
        return None

    if not categories:
        raise ValueError(
            f"{label} must not be an empty list. Pass None to include every {label}, "
            f"or pass at least one {enum_cls.__name__} value."
        )

    _RemoteDatasetLoader._validate_enums(list(categories), enum_cls, label)
    return {cat.value for cat in categories}


def _common_metadata_for_country(country: XLSafetyBenchCountry) -> dict[str, str]:
    """
    Return base metadata fields shared by every seed prompt for a country.

    Args:
        country (XLSafetyBenchCountry): The country the row belongs to.

    Returns:
        dict[str, str]: Country slug, display name, language ISO code, and language name.
    """
    info = _COUNTRY_INFO[country]
    return {
        "country": country.value,
        "country_display_name": info.country_display_name,
        "language": info.language_display_name,
        "language_iso_code": info.iso_639_1_code,
    }


class _XLSafetyBenchJailbreakDataset(_RemoteDatasetLoader):
    """
    Loader for the Jailbreak track of XL-SafetyBench.

    XL-SafetyBench is a country-grounded multilingual safety benchmark covering 10
    country-language pairs. The Jailbreak track contains 4,500 adversarial prompts
    (450 per country) across five harm categories, each grounded in the country's
    local context (platforms, legal frameworks, sociopolitical structures, etc.).

    Reference: [@choi2026xlsafetybench]
    Paper: https://arxiv.org/abs/2605.05662
    HuggingFace: https://huggingface.co/datasets/AIM-Intelligence/XL-SafetyBench
    License: CC-BY-4.0

    Content Warning: This dataset contains adversarial prompts intended to elicit
    harmful or country-specific harmful content. Consult your legal department before
    using these prompts against production LLMs.
    """

    harm_categories: list[str] = [c.value for c in XLSafetyBenchJailbreakCategory]
    modalities: list[str] = ["text"]
    size: str = "large"
    tags: set[str] = {"default", "safety", "jailbreak", "multilingual", "country_grounded"}

    def __init__(
        self,
        *,
        countries: Optional[list[XLSafetyBenchCountry]] = None,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        """
        Initialize the XL-SafetyBench Jailbreak dataset loader.

        Args:
            countries (Optional[list[XLSafetyBenchCountry]]): Subset of country-language
                pairs to include. Defaults to ``None`` (all 10 countries).
            categories (Optional[list[XLSafetyBenchJailbreakCategory]]): Subset of harm
                categories to include. Defaults to ``None`` (all 5 categories).

        Raises:
            ValueError: If ``countries`` or ``categories`` is an empty list or contains
                values that are not members of the expected enum.
        """
        self._countries = _resolve_countries(countries)
        self._categories_filter = _resolve_category_filter(
            categories=categories,
            enum_cls=XLSafetyBenchJailbreakCategory,
            label="category",
        )
        self.source = _HF_DATASET_URL

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch XL-SafetyBench jailbreak prompts and return them as a SeedDataset.

        Each row is loaded from the per-country ``data/jailbreak/<country>/attack_prompts.csv``
        files. The original ``attack_prompt`` (in the country's language) is used as the
        SeedPrompt value; the ``base_query`` and other context fields are preserved in
        ``SeedPrompt.metadata`` so downstream judges can reconstruct the paper's evaluation
        without re-fetching.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered XL-SafetyBench jailbreak prompts.

        Raises:
            ValueError: If no prompts remain after filtering.
        """
        logger.info(
            "Loading XL-SafetyBench Jailbreak dataset (countries=%s, categories=%s)",
            [c.value for c in self._countries],
            sorted(self._categories_filter) if self._categories_filter is not None else "all",
        )

        seed_prompts: list[SeedPrompt] = []
        for country in self._countries:
            seed_prompts.extend(self._load_country(country=country, cache=cache))

        if not seed_prompts:
            raise ValueError(
                "No XL-SafetyBench jailbreak prompts matched the configured filters. "
                "Check the country/category arguments."
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from XL-SafetyBench Jailbreak dataset")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _load_country(
        self,
        *,
        country: XLSafetyBenchCountry,
        cache: bool,
    ) -> list[SeedPrompt]:
        """
        Load and convert a single country's attack prompts.

        Args:
            country (XLSafetyBenchCountry): The country whose split to load.
            cache (bool): Whether to cache the fetched CSV file.

        Returns:
            list[SeedPrompt]: SeedPrompts for the country, filtered by ``categories``.
        """
        url = f"{_HF_RESOLVE_BASE}/data/jailbreak/{country.value}/attack_prompts.csv"
        rows = self._fetch_from_url(source=url, source_type="public_url", cache=cache)

        country_metadata = _common_metadata_for_country(country)
        seed_prompts: list[SeedPrompt] = []
        for row in rows:
            category = str(row.get("category", "")).strip()
            if self._categories_filter is not None and category not in self._categories_filter:
                continue

            attack_prompt = str(row.get("attack_prompt", "")).strip()
            if not attack_prompt:
                logger.warning(
                    "[XLSafetyBench/Jailbreak] Skipping row with empty attack_prompt (id=%s, country=%s)",
                    row.get("id", "<unknown>"),
                    country.value,
                )
                continue

            row_id = str(row.get("id", "")).strip()
            metadata: dict[str, str | int] = {
                **country_metadata,
                "row_id": row_id,
                "category": category,
                "subcategory_english": str(row.get("subcategory_english", "")),
                "subcategory_local": str(row.get("subcategory_local", "")),
                "base_query_english": str(row.get("base_query_english", "")),
                "base_query_local": str(row.get("base_query_local", "")),
                "track": "jailbreak",
            }

            seed_prompts.append(
                SeedPrompt(
                    value=attack_prompt,
                    data_type="text",
                    name=f"XL-SafetyBench Jailbreak {row_id}".strip(),
                    dataset_name=self.dataset_name,
                    harm_categories=[category] if category else [],
                    groups=_GROUPS,
                    authors=_AUTHORS,
                    description=(
                        "Adversarial prompt from the Jailbreak track of XL-SafetyBench, a "
                        "country-grounded multilingual safety benchmark. The prompt is in "
                        f"{country_metadata['language']} for "
                        f"{country_metadata['country_display_name']} and was authored to bypass "
                        "safety mechanisms while targeting a country-specific harm. "
                        f"Paper: {_PAPER_URL}"
                    ),
                    source=self.source,
                    metadata=metadata,
                )
            )

        return seed_prompts


class _XLSafetyBenchCulturalDataset(_RemoteDatasetLoader):
    """
    Loader for the Cultural track of XL-SafetyBench.

    The Cultural track contains 1,000 scenarios (100 per country) where a culturally
    sensitive element is embedded inside an otherwise innocuous request. The model is
    expected to recognize the hidden cultural violation rather than blindly fulfill
    the surface-level task.

    Reference: [@choi2026xlsafetybench]
    Paper: https://arxiv.org/abs/2605.05662
    HuggingFace: https://huggingface.co/datasets/AIM-Intelligence/XL-SafetyBench
    License: CC-BY-4.0

    Note: The scenarios themselves are *not* adversarial — they contain naturally
    phrased requests. This dataset is therefore intentionally *not* tagged with
    ``default``: it does not belong in default safety sweeps that assume the prompts
    are adversarial.
    """

    harm_categories: list[str] = [c.value for c in XLSafetyBenchCulturalCategory]
    modalities: list[str] = ["text"]
    size: str = "large"
    tags: set[str] = {"safety", "cultural", "multilingual", "country_grounded"}

    def __init__(
        self,
        *,
        countries: Optional[list[XLSafetyBenchCountry]] = None,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        """
        Initialize the XL-SafetyBench Cultural dataset loader.

        Args:
            countries (Optional[list[XLSafetyBenchCountry]]): Subset of country-language
                pairs to include. Defaults to ``None`` (all 10 countries).
            categories (Optional[list[XLSafetyBenchCulturalCategory]]): Subset of cultural
                categories to include. Defaults to ``None`` (all 6 categories).
            language_mode (Literal["local", "english"]): Which version of the scenario
                text to use as the prompt value. ``"local"`` (default) matches the paper's
                evaluation setup; ``"english"`` is useful for cross-language probing.

        Raises:
            ValueError: If ``countries`` or ``categories`` is an empty list, contains
                values that are not members of the expected enum, or if ``language_mode``
                is not one of the supported values.
        """
        if language_mode not in ("local", "english"):
            raise ValueError(f"language_mode must be 'local' or 'english', got {language_mode!r}.")

        self._countries = _resolve_countries(countries)
        self._categories_filter = _resolve_category_filter(
            categories=categories,
            enum_cls=XLSafetyBenchCulturalCategory,
            label="category",
        )
        self._language_mode: Literal["local", "english"] = language_mode
        self.source = _HF_DATASET_URL

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch XL-SafetyBench cultural scenarios and return them as a SeedDataset.

        Each row is loaded from ``data/cultural/<country>/scenario_prompts.csv``. The
        scenario text (``scenario_local`` or ``scenario_english`` depending on
        ``language_mode``) is used as the SeedPrompt value; the cultural sensitivity,
        base query, and ``hidden_violation`` ground-truth label are preserved in
        ``SeedPrompt.metadata`` so downstream judges and human analysts have full context.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered XL-SafetyBench cultural scenarios.

        Raises:
            ValueError: If no scenarios remain after filtering.
        """
        logger.info(
            "Loading XL-SafetyBench Cultural dataset (countries=%s, categories=%s, language_mode=%s)",
            [c.value for c in self._countries],
            sorted(self._categories_filter) if self._categories_filter is not None else "all",
            self._language_mode,
        )

        seed_prompts: list[SeedPrompt] = []
        for country in self._countries:
            seed_prompts.extend(self._load_country(country=country, cache=cache))

        if not seed_prompts:
            raise ValueError(
                "No XL-SafetyBench cultural scenarios matched the configured filters. "
                "Check the country/category arguments."
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} scenarios from XL-SafetyBench Cultural dataset")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _load_country(
        self,
        *,
        country: XLSafetyBenchCountry,
        cache: bool,
    ) -> list[SeedPrompt]:
        """
        Load and convert a single country's cultural scenarios.

        Args:
            country (XLSafetyBenchCountry): The country whose split to load.
            cache (bool): Whether to cache the fetched CSV file.

        Returns:
            list[SeedPrompt]: SeedPrompts for the country, filtered by ``categories``.
        """
        url = f"{_HF_RESOLVE_BASE}/data/cultural/{country.value}/scenario_prompts.csv"
        rows = self._fetch_from_url(source=url, source_type="public_url", cache=cache)

        country_metadata = _common_metadata_for_country(country)
        scenario_key = "scenario_local" if self._language_mode == "local" else "scenario_english"

        seed_prompts: list[SeedPrompt] = []
        for row in rows:
            category = str(row.get("category", "")).strip()
            if self._categories_filter is not None and category not in self._categories_filter:
                continue

            scenario_text = str(row.get(scenario_key, "")).strip()
            if not scenario_text:
                logger.warning(
                    "[XLSafetyBench/Cultural] Skipping row with empty %s (id=%s, country=%s)",
                    scenario_key,
                    row.get("id", "<unknown>"),
                    country.value,
                )
                continue

            row_id = str(row.get("id", "")).strip()
            metadata: dict[str, str | int] = {
                **country_metadata,
                "row_id": row_id,
                "category": category,
                "sensitivity_english": str(row.get("sensitivity_english", "")),
                "sensitivity_local": str(row.get("sensitivity_local", "")),
                "base_query_english": str(row.get("base_query_english", "")),
                "base_query_local": str(row.get("base_query_local", "")),
                "scenario_english": str(row.get("scenario_english", "")),
                "scenario_local": str(row.get("scenario_local", "")),
                "hidden_violation": str(row.get("hidden_violation", "")),
                "language_mode": self._language_mode,
                "track": "cultural",
            }

            seed_prompts.append(
                SeedPrompt(
                    value=scenario_text,
                    data_type="text",
                    name=f"XL-SafetyBench Cultural {row_id}".strip(),
                    dataset_name=self.dataset_name,
                    harm_categories=[category] if category else [],
                    groups=_GROUPS,
                    authors=_AUTHORS,
                    description=(
                        "Culturally embedded scenario from the Cultural track of XL-SafetyBench. "
                        "The request is intentionally innocuous on the surface but contains a "
                        f"hidden cultural sensitivity specific to {country_metadata['country_display_name']}. "
                        "A culturally aware assistant should recognize and flag the embedded "
                        f"sensitivity. Paper: {_PAPER_URL}"
                    ),
                    source=self.source,
                    metadata=metadata,
                )
            )

        return seed_prompts


# --- Per-country jailbreak siblings -------------------------------------------------
#
# Each country in XL-SafetyBench is a distinct CSV under
# ``data/jailbreak/<country>/safety_attack_prompts.csv``. Each sibling pins
# ``countries`` to a single value so that the daily e2e dataset sweep exercises
# every upstream file (the parent's no-args default also iterates all 10, but
# the siblings give per-country e2e isolation, easier failure attribution, and
# a more ergonomic "just one country" API).


class _XLSafetyBenchJailbreakFranceDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the France split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.FRANCE], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_france"


class _XLSafetyBenchJailbreakGermanyDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the Germany split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.GERMANY], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_germany"


class _XLSafetyBenchJailbreakIndiaDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the India split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.INDIA], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_india"


class _XLSafetyBenchJailbreakIndonesiaDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the Indonesia split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.INDONESIA], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_indonesia"


class _XLSafetyBenchJailbreakJapanDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the Japan split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.JAPAN], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_japan"


class _XLSafetyBenchJailbreakSouthKoreaDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the South Korea split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.SOUTH_KOREA], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_south_korea"


class _XLSafetyBenchJailbreakSpainDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the Spain split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.SPAIN], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_spain"


class _XLSafetyBenchJailbreakTurkeyDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the Turkey split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.TURKEY], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_turkey"


class _XLSafetyBenchJailbreakUnitedArabEmiratesDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the United Arab Emirates split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.UNITED_ARAB_EMIRATES], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_united_arab_emirates"


class _XLSafetyBenchJailbreakUnitedStatesDataset(_XLSafetyBenchJailbreakDataset):
    """Sibling loader pinned to the United States split of XL-SafetyBench Jailbreak."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchJailbreakCategory]] = None,
    ) -> None:
        super().__init__(countries=[XLSafetyBenchCountry.UNITED_STATES], categories=categories)

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_jailbreak_united_states"


# --- Per-country cultural siblings -------------------------------------------------
#
# Each country in XL-SafetyBench Cultural is a distinct CSV under
# ``data/cultural/<country>/scenario_prompts.csv``. Each sibling pins
# ``countries`` to a single value. ``language_mode`` is still a sibling-level
# kwarg because it selects between two columns in the SAME CSV (no new upstream
# artifact), so it does not warrant a separate sibling per mode.


class _XLSafetyBenchCulturalFranceDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the France split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.FRANCE],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_france"


class _XLSafetyBenchCulturalGermanyDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the Germany split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.GERMANY],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_germany"


class _XLSafetyBenchCulturalIndiaDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the India split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.INDIA],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_india"


class _XLSafetyBenchCulturalIndonesiaDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the Indonesia split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.INDONESIA],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_indonesia"


class _XLSafetyBenchCulturalJapanDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the Japan split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.JAPAN],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_japan"


class _XLSafetyBenchCulturalSouthKoreaDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the South Korea split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.SOUTH_KOREA],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_south_korea"


class _XLSafetyBenchCulturalSpainDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the Spain split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.SPAIN],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_spain"


class _XLSafetyBenchCulturalTurkeyDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the Turkey split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.TURKEY],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_turkey"


class _XLSafetyBenchCulturalUnitedArabEmiratesDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the United Arab Emirates split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.UNITED_ARAB_EMIRATES],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_united_arab_emirates"


class _XLSafetyBenchCulturalUnitedStatesDataset(_XLSafetyBenchCulturalDataset):
    """Sibling loader pinned to the United States split of XL-SafetyBench Cultural."""

    size: str = "medium"

    def __init__(
        self,
        *,
        categories: Optional[list[XLSafetyBenchCulturalCategory]] = None,
        language_mode: Literal["local", "english"] = "local",
    ) -> None:
        super().__init__(
            countries=[XLSafetyBenchCountry.UNITED_STATES],
            categories=categories,
            language_mode=language_mode,
        )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "xl_safety_bench_cultural_united_states"
