# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Loaders for the XL-SafetyBench Jailbreak and Cultural benchmarks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt

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
    "BMW Group",
    "Coinbase",
    "Technical University of Munich",
    "Ankara University",
    "Cyril Amarchand Mangaldas",
    "Seoul National University",
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


class XLSafetyBenchLanguageMode(str, Enum):
    """Which version of a per-row scenario text to use as the SeedPrompt value."""

    LOCAL = "local"
    ENGLISH = "english"


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


def _resolve_countries(countries: list[XLSafetyBenchCountry] | None) -> list[XLSafetyBenchCountry]:
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
    categories: Sequence[Enum] | None,
    enum_cls: type[Enum],
    label: str,
) -> set[str] | None:
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


def _normalize_csv_row(row: dict[str, str]) -> dict[str, str]:
    """
    Strip a UTF-8 BOM (U+FEFF) from any column header in a CSV row.

    HuggingFace ships the XL-SafetyBench CSVs with a BOM, so the first column's key
    arrives as ``"\ufeffid"`` rather than ``"id"``. Stripping it here keeps the rest
    of the loader code dialect-agnostic.

    Args:
        row (dict[str, str]): A row dict produced by ``csv.DictReader``.

    Returns:
        dict[str, str]: The same row with any BOM-prefixed key stripped.
    """
    return {(k.lstrip("\ufeff") if k else k): v for k, v in row.items()}


def _row_value(row: dict[str, str], key: str) -> str:
    """
    Return a CSV cell as a stripped string, treating missing/None cells as empty.

    ``csv.DictReader`` yields ``None`` for cells that come from short rows; calling
    ``str(None)`` would silently propagate the literal text ``"None"`` into seed
    metadata, so we coalesce ``None`` to ``""`` before stripping.

    Args:
        row (dict[str, str]): A row dict from ``csv.DictReader`` (already normalized).
        key (str): The column to look up.

    Returns:
        str: The cell value, stripped of surrounding whitespace, or ``""`` if the
            column is missing or its cell is ``None``.
    """
    return str(row.get(key) or "").strip()


def _validate_csv_schema(*, rows: list[dict[str, str]], required_columns: list[str], url: str) -> None:
    """
    Validate that a fetched CSV exposes every column the loader depends on.

    The check inspects the first row's keys (after BOM stripping) and raises if any
    required column is missing. This surfaces upstream HF schema drift up front with
    one clear error message, instead of silently emitting empty seeds for every
    affected row.

    Args:
        rows (list[dict[str, str]]): Rows as returned by ``_fetch_from_url``.
        required_columns (list[str]): Columns whose absence should fail the load.
        url (str): The source URL, included in the error message for triage.

    Raises:
        ValueError: If any required column is absent from the CSV header.
    """
    if not rows:
        # An empty CSV is handled by the loader's empty-result check; nothing to validate.
        return

    found_columns = {(k.lstrip("\ufeff") if k else k) for k in rows[0] if k is not None}
    missing = [c for c in required_columns if c not in found_columns]
    if missing:
        raise ValueError(
            f"XL-SafetyBench CSV at {url} is missing required column(s) {missing}. "
            f"Required columns: {required_columns}. Found columns: {sorted(found_columns)}. "
            f"The upstream HuggingFace dataset schema may have changed."
        )


_JAILBREAK_REQUIRED_COLUMNS: list[str] = [
    "id",
    "category",
    "base_query_local",
    "base_query_english",
    "attack_prompt",
]
_JAILBREAK_OBJECTIVES_REQUIRED_COLUMNS: list[str] = [
    "id",
    "category",
    "base_query_local",
    "base_query_english",
]
_CULTURAL_REQUIRED_COLUMNS: list[str] = [
    "id",
    "category",
    "scenario_local",
    "scenario_english",
    "hidden_violation",
]


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
        countries: list[XLSafetyBenchCountry] | None = None,
        categories: list[XLSafetyBenchJailbreakCategory] | None = None,
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
        """The dataset name."""
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
        _validate_csv_schema(rows=rows, required_columns=_JAILBREAK_REQUIRED_COLUMNS, url=url)

        country_metadata = _common_metadata_for_country(country)
        seed_prompts: list[SeedPrompt] = []
        for raw_row in rows:
            row = _normalize_csv_row(raw_row)
            category = _row_value(row, "category")
            if self._categories_filter is not None and category not in self._categories_filter:
                continue

            attack_prompt = _row_value(row, "attack_prompt")
            if not attack_prompt:
                logger.warning(
                    "[XLSafetyBench/Jailbreak] Skipping row with empty attack_prompt (id=%s, country=%s)",
                    row.get("id") or "<unknown>",
                    country.value,
                )
                continue

            row_id = _row_value(row, "id")
            metadata: dict[str, str | int] = {
                **country_metadata,
                "row_id": row_id,
                "category": category,
                "subcategory_english": _row_value(row, "subcategory_english"),
                "subcategory_local": _row_value(row, "subcategory_local"),
                "base_query_english": _row_value(row, "base_query_english"),
                "base_query_local": _row_value(row, "base_query_local"),
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


class _XLSafetyBenchJailbreakObjectivesDataset(_RemoteDatasetLoader):
    """
    Objectives view of the Jailbreak track of XL-SafetyBench.

    Each row of the per-country ``attack_prompts.csv`` files pairs a fully crafted
    adversarial ``attack_prompt`` with the underlying harmful goal it tries to elicit
    (``base_query_local`` / ``base_query_english``). This loader exposes the *goals*
    as ``SeedObjective`` instances so PyRIT attack strategies can run their own
    multi-turn jailbreaks against them, independent of the paper's bundled attack
    prompts.

    The CSV ships ~3 attack prompts per unique base query per country (450 prompts
    cover ~150 goals × 10 countries → roughly 1,500 unique objectives). This loader
    deduplicates by ``(country, base_query_local)`` and prefers the local-language
    text as ``SeedObjective.value`` (the English version is preserved in metadata).

    The CSV's other variant is ``_XLSafetyBenchJailbreakDataset``, which emits
    the polished attack prompts themselves as ``SeedPrompt`` instances.

    Reference: [@choi2026xlsafetybench]
    Paper: https://arxiv.org/abs/2605.05662
    HuggingFace: https://huggingface.co/datasets/AIM-Intelligence/XL-SafetyBench
    License: CC-BY-4.0

    Content Warning: The base queries describe country-specific harmful intents.
    """

    harm_categories: list[str] = [c.value for c in XLSafetyBenchJailbreakCategory]
    modalities: list[str] = ["text"]
    size: str = "large"
    tags: set[str] = {"safety", "jailbreak", "multilingual", "country_grounded", "objectives"}

    def __init__(
        self,
        *,
        countries: list[XLSafetyBenchCountry] | None = None,
        categories: list[XLSafetyBenchJailbreakCategory] | None = None,
    ) -> None:
        """
        Initialize the XL-SafetyBench Jailbreak Objectives dataset loader.

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
        """The dataset name."""
        return "xl_safety_bench_jailbreak_objectives"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch XL-SafetyBench jailbreak objectives and return them as a SeedDataset.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered XL-SafetyBench jailbreak
            objectives, deduplicated by ``(country, base_query_local)``.

        Raises:
            ValueError: If no objectives remain after filtering.
        """
        logger.info(
            "Loading XL-SafetyBench Jailbreak objectives (countries=%s, categories=%s)",
            [c.value for c in self._countries],
            sorted(self._categories_filter) if self._categories_filter is not None else "all",
        )

        seeds: list[SeedObjective] = []
        for country in self._countries:
            seeds.extend(self._load_country(country=country, cache=cache))

        if not seeds:
            raise ValueError(
                "No XL-SafetyBench jailbreak objectives matched the configured filters. "
                "Check the country/category arguments."
            )

        logger.info(f"Successfully loaded {len(seeds)} objectives from XL-SafetyBench Jailbreak dataset")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    def _load_country(
        self,
        *,
        country: XLSafetyBenchCountry,
        cache: bool,
    ) -> list[SeedObjective]:
        """
        Load and dedupe a single country's base queries into SeedObjective instances.

        Args:
            country (XLSafetyBenchCountry): The country whose split to load.
            cache (bool): Whether to cache the fetched CSV file.

        Returns:
            list[SeedObjective]: SeedObjectives for the country, filtered by
            ``categories`` and deduplicated by ``base_query_local``.
        """
        url = f"{_HF_RESOLVE_BASE}/data/jailbreak/{country.value}/attack_prompts.csv"
        rows = self._fetch_from_url(source=url, source_type="public_url", cache=cache)
        _validate_csv_schema(rows=rows, required_columns=_JAILBREAK_OBJECTIVES_REQUIRED_COLUMNS, url=url)

        country_metadata = _common_metadata_for_country(country)
        seen_objectives: dict[str, SeedObjective] = {}
        for raw_row in rows:
            row = _normalize_csv_row(raw_row)
            category = _row_value(row, "category")
            if self._categories_filter is not None and category not in self._categories_filter:
                continue

            base_query_local = _row_value(row, "base_query_local")
            base_query_english = _row_value(row, "base_query_english")
            objective_text = base_query_local or base_query_english
            if not objective_text:
                logger.warning(
                    "[XLSafetyBench/JailbreakObjectives] Skipping row with empty base_query (id=%s, country=%s)",
                    row.get("id") or "<unknown>",
                    country.value,
                )
                continue

            if objective_text in seen_objectives:
                continue

            row_id = _row_value(row, "id")
            metadata: dict[str, str | int] = {
                **country_metadata,
                "row_id": row_id,
                "category": category,
                "subcategory_english": _row_value(row, "subcategory_english"),
                "subcategory_local": _row_value(row, "subcategory_local"),
                "base_query_english": base_query_english,
                "base_query_local": base_query_local,
                "track": "jailbreak_objectives",
            }

            seen_objectives[objective_text] = SeedObjective(
                value=objective_text,
                name=f"XL-SafetyBench Jailbreak Objective {country.value} {row_id}".strip(),
                dataset_name=self.dataset_name,
                harm_categories=[category] if category else [],
                groups=_GROUPS,
                authors=_AUTHORS,
                description=(
                    "Harmful base query from the Jailbreak track of XL-SafetyBench, a "
                    "country-grounded multilingual safety benchmark. The query represents the "
                    f"underlying goal that the paper's attack prompts aim to elicit in "
                    f"{country_metadata['language']} for {country_metadata['country_display_name']}. "
                    f"Paper: {_PAPER_URL}"
                ),
                source=self.source,
                metadata=metadata,
            )

        return list(seen_objectives.values())


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

    The per-seed ``language`` metadata field always names the country's local
    language (the cultural context being probed), not the language of the actual
    scenario text — so even with ``language_mode="english"`` rows for Japan still
    carry ``language="Japanese"``.
    """

    harm_categories: list[str] = [c.value for c in XLSafetyBenchCulturalCategory]
    modalities: list[str] = ["text"]
    size: str = "large"
    tags: set[str] = {"safety", "cultural", "multilingual", "country_grounded"}

    def __init__(
        self,
        *,
        countries: list[XLSafetyBenchCountry] | None = None,
        categories: list[XLSafetyBenchCulturalCategory] | None = None,
        language_mode: XLSafetyBenchLanguageMode = XLSafetyBenchLanguageMode.LOCAL,
    ) -> None:
        """
        Initialize the XL-SafetyBench Cultural dataset loader.

        Args:
            countries (Optional[list[XLSafetyBenchCountry]]): Subset of country-language
                pairs to include. Defaults to ``None`` (all 10 countries).
            categories (Optional[list[XLSafetyBenchCulturalCategory]]): Subset of cultural
                categories to include. Defaults to ``None`` (all 6 categories).
            language_mode (XLSafetyBenchLanguageMode): Which version of the scenario
                text to use as the prompt value. ``LOCAL`` (default) matches the paper's
                evaluation setup; ``ENGLISH`` is useful for cross-language probing.

        Raises:
            ValueError: If ``countries`` or ``categories`` is an empty list, contains
                values that are not members of the expected enum, or if ``language_mode``
                is not a member of ``XLSafetyBenchLanguageMode``.
        """
        if not isinstance(language_mode, XLSafetyBenchLanguageMode):
            raise ValueError(f"language_mode must be an XLSafetyBenchLanguageMode member, got {language_mode!r}.")

        self._countries = _resolve_countries(countries)
        self._categories_filter = _resolve_category_filter(
            categories=categories,
            enum_cls=XLSafetyBenchCulturalCategory,
            label="category",
        )
        self._language_mode: XLSafetyBenchLanguageMode = language_mode
        self.source = _HF_DATASET_URL

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
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
            self._language_mode.value,
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
        _validate_csv_schema(rows=rows, required_columns=_CULTURAL_REQUIRED_COLUMNS, url=url)

        country_metadata = _common_metadata_for_country(country)
        scenario_key = (
            "scenario_local" if self._language_mode is XLSafetyBenchLanguageMode.LOCAL else "scenario_english"
        )

        seed_prompts: list[SeedPrompt] = []
        for raw_row in rows:
            row = _normalize_csv_row(raw_row)
            category = _row_value(row, "category")
            if self._categories_filter is not None and category not in self._categories_filter:
                continue

            scenario_text = _row_value(row, scenario_key)
            if not scenario_text:
                logger.warning(
                    "[XLSafetyBench/Cultural] Skipping row with empty %s (id=%s, country=%s)",
                    scenario_key,
                    row.get("id") or "<unknown>",
                    country.value,
                )
                continue

            row_id = _row_value(row, "id")
            metadata: dict[str, str | int] = {
                **country_metadata,
                "row_id": row_id,
                "category": category,
                "sensitivity_english": _row_value(row, "sensitivity_english"),
                "sensitivity_local": _row_value(row, "sensitivity_local"),
                "base_query_english": _row_value(row, "base_query_english"),
                "base_query_local": _row_value(row, "base_query_local"),
                "scenario_english": _row_value(row, "scenario_english"),
                "scenario_local": _row_value(row, "scenario_local"),
                "hidden_violation": _row_value(row, "hidden_violation"),
                "language_mode": self._language_mode.value,
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
