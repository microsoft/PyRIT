# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset import (
    XLSafetyBenchCountry,
    XLSafetyBenchCulturalCategory,
    XLSafetyBenchJailbreakCategory,
    _XLSafetyBenchCulturalDataset,
    _XLSafetyBenchJailbreakDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


def _jailbreak_rows(country_label: str) -> list[dict[str, str]]:
    return [
        {
            "id": f"JB-{country_label}-1",
            "category": "Criminal Activities",
            "subcategory_english": "Fraud",
            "subcategory_local": "Fraude",
            "base_query_english": f"Base query 1 for {country_label}",
            "base_query_local": f"Local base 1 for {country_label}",
            "attack_prompt": f"Adversarial attack 1 ({country_label})",
        },
        {
            "id": f"JB-{country_label}-2",
            "category": "Hate & Discrimination",
            "subcategory_english": "Slur",
            "subcategory_local": "Insulto",
            "base_query_english": f"Base query 2 for {country_label}",
            "base_query_local": f"Local base 2 for {country_label}",
            "attack_prompt": f"Adversarial attack 2 ({country_label})",
        },
    ]


def _cultural_rows(country_label: str) -> list[dict[str, str]]:
    return [
        {
            "id": f"CC-{country_label}-1",
            "category": "Symbolic Taboos & Gift-Giving",
            "sensitivity_english": "Gift taboo",
            "sensitivity_local": "Tabou cadeau",
            "base_query_english": "Suggest a gift",
            "base_query_local": "Suggère un cadeau",
            "scenario_english": f"English scenario 1 for {country_label}",
            "scenario_local": f"Local scenario 1 for {country_label}",
            "hidden_violation": f"Taboo gift for {country_label}",
        },
        {
            "id": f"CC-{country_label}-2",
            "category": "Legal Landmines",
            "sensitivity_english": "Restricted import",
            "sensitivity_local": "Importation restreinte",
            "base_query_english": "Help me ship a package",
            "base_query_local": "Aide-moi à envoyer un colis",
            "scenario_english": f"English scenario 2 for {country_label}",
            "scenario_local": f"Local scenario 2 for {country_label}",
            "hidden_violation": f"Illegal item in {country_label}",
        },
    ]


def _patch_jailbreak_fetch(loader: _XLSafetyBenchJailbreakDataset):
    """Patch the loader's URL fetch so each call returns rows based on the URL country slug."""

    def side_effect(*, source: str, source_type: str, cache: bool) -> list[dict[str, str]]:
        # URLs look like .../data/jailbreak/<country>/attack_prompts.csv
        country = source.split("/data/jailbreak/")[1].split("/")[0]
        return _jailbreak_rows(country)

    return patch.object(loader, "_fetch_from_url", side_effect=side_effect)


def _patch_cultural_fetch(loader: _XLSafetyBenchCulturalDataset):
    """Patch the loader's URL fetch so each call returns rows based on the URL country slug."""

    def side_effect(*, source: str, source_type: str, cache: bool) -> list[dict[str, str]]:
        country = source.split("/data/cultural/")[1].split("/")[0]
        return _cultural_rows(country)

    return patch.object(loader, "_fetch_from_url", side_effect=side_effect)


def test_jailbreak_dataset_name():
    loader = _XLSafetyBenchJailbreakDataset()
    assert loader.dataset_name == "xl_safety_bench_jailbreak"


def test_cultural_dataset_name():
    loader = _XLSafetyBenchCulturalDataset()
    assert loader.dataset_name == "xl_safety_bench_cultural"


def test_dataset_metadata_tags():
    # The jailbreak set is adversarial, so it belongs in default sweeps.
    assert "default" in _XLSafetyBenchJailbreakDataset.tags
    # The cultural set is innocuous-by-construction and must NOT be in default sweeps.
    assert "default" not in _XLSafetyBenchCulturalDataset.tags
    assert "cultural" in _XLSafetyBenchCulturalDataset.tags
    assert "country_grounded" in _XLSafetyBenchCulturalDataset.tags


async def test_jailbreak_loads_all_countries_by_default():
    loader = _XLSafetyBenchJailbreakDataset()

    with _patch_jailbreak_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    # 10 countries × 2 mock rows each.
    assert len(dataset.seeds) == 20
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    countries_seen = {p.metadata["country"] for p in dataset.seeds}
    assert len(countries_seen) == 10


async def test_jailbreak_country_filter():
    loader = _XLSafetyBenchJailbreakDataset(
        countries=[XLSafetyBenchCountry.JAPAN, XLSafetyBenchCountry.GERMANY],
    )

    with _patch_jailbreak_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 4  # 2 countries × 2 rows
    countries_seen = {p.metadata["country"] for p in dataset.seeds}
    assert countries_seen == {"japan", "germany"}


async def test_jailbreak_category_filter():
    loader = _XLSafetyBenchJailbreakDataset(
        countries=[XLSafetyBenchCountry.FRANCE],
        categories=[XLSafetyBenchJailbreakCategory.CRIMINAL_ACTIVITIES],
    )

    with _patch_jailbreak_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].metadata["category"] == "Criminal Activities"
    assert dataset.seeds[0].value == "Adversarial attack 1 (france)"


async def test_jailbreak_metadata_propagation():
    loader = _XLSafetyBenchJailbreakDataset(
        countries=[XLSafetyBenchCountry.SPAIN],
        categories=[XLSafetyBenchJailbreakCategory.HATE_AND_DISCRIMINATION],
    )

    with _patch_jailbreak_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    seed = dataset.seeds[0]
    md = seed.metadata
    assert md["country"] == "spain"
    assert md["country_display_name"] == "Spain"
    assert md["language"] == "Spanish"
    assert md["language_iso_code"] == "es"
    assert md["base_query_local"] == "Local base 2 for spain"
    assert md["base_query_english"] == "Base query 2 for spain"
    assert md["track"] == "jailbreak"
    assert seed.harm_categories == ["Hate & Discrimination"]
    assert seed.dataset_name == "xl_safety_bench_jailbreak"


async def test_jailbreak_skips_empty_attack_prompts():
    loader = _XLSafetyBenchJailbreakDataset(countries=[XLSafetyBenchCountry.FRANCE])
    rows_with_blank = [
        {
            "id": "JB-fr-blank",
            "category": "Criminal Activities",
            "subcategory_english": "X",
            "subcategory_local": "Y",
            "base_query_english": "bq",
            "base_query_local": "bql",
            "attack_prompt": "   ",
        },
        {
            "id": "JB-fr-real",
            "category": "Criminal Activities",
            "subcategory_english": "X",
            "subcategory_local": "Y",
            "base_query_english": "bq",
            "base_query_local": "bql",
            "attack_prompt": "Real attack",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows_with_blank):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].value == "Real attack"


async def test_jailbreak_raises_when_filter_matches_nothing():
    loader = _XLSafetyBenchJailbreakDataset(
        countries=[XLSafetyBenchCountry.FRANCE],
        categories=[XLSafetyBenchJailbreakCategory.POLITICAL_AND_MISINFORMATION],
    )

    with _patch_jailbreak_fetch(loader):
        with pytest.raises(ValueError, match="No XL-SafetyBench jailbreak prompts"):
            await loader.fetch_dataset_async()


def test_jailbreak_rejects_empty_filters():
    with pytest.raises(ValueError, match="countries must not be an empty list"):
        _XLSafetyBenchJailbreakDataset(countries=[])
    with pytest.raises(ValueError, match="category must not be an empty list"):
        _XLSafetyBenchJailbreakDataset(categories=[])


def test_jailbreak_rejects_wrong_enum_type():
    with pytest.raises(ValueError, match="Expected XLSafetyBenchCountry"):
        _XLSafetyBenchJailbreakDataset(countries=["france"])  # type: ignore[list-item]


def test_cultural_rejects_invalid_language_mode():
    with pytest.raises(ValueError, match="language_mode must be 'local' or 'english'"):
        _XLSafetyBenchCulturalDataset(language_mode="japanese")  # type: ignore[arg-type]


async def test_cultural_default_uses_local_scenario():
    loader = _XLSafetyBenchCulturalDataset(countries=[XLSafetyBenchCountry.FRANCE])

    with _patch_cultural_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    # value is scenario_local
    values = {p.value for p in dataset.seeds}
    assert values == {"Local scenario 1 for france", "Local scenario 2 for france"}
    assert all(p.metadata["language_mode"] == "local" for p in dataset.seeds)
    assert all(p.metadata["language"] == "French" for p in dataset.seeds)


async def test_cultural_english_language_mode():
    loader = _XLSafetyBenchCulturalDataset(
        countries=[XLSafetyBenchCountry.FRANCE],
        language_mode="english",
    )

    with _patch_cultural_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    values = {p.value for p in dataset.seeds}
    assert values == {"English scenario 1 for france", "English scenario 2 for france"}
    assert all(p.metadata["language_mode"] == "english" for p in dataset.seeds)


async def test_cultural_category_filter_and_hidden_violation():
    loader = _XLSafetyBenchCulturalDataset(
        countries=[XLSafetyBenchCountry.JAPAN],
        categories=[XLSafetyBenchCulturalCategory.LEGAL_LANDMINES],
    )

    with _patch_cultural_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    seed = dataset.seeds[0]
    assert seed.metadata["category"] == "Legal Landmines"
    assert seed.metadata["hidden_violation"] == "Illegal item in japan"
    assert seed.metadata["country_display_name"] == "Japan"
    assert seed.metadata["language"] == "Japanese"
    # Both scenario texts must remain accessible via metadata regardless of language_mode.
    assert seed.metadata["scenario_local"] == "Local scenario 2 for japan"
    assert seed.metadata["scenario_english"] == "English scenario 2 for japan"


async def test_cultural_skips_empty_scenario():
    loader = _XLSafetyBenchCulturalDataset(countries=[XLSafetyBenchCountry.FRANCE])
    rows = [
        {
            "id": "CC-fr-blank",
            "category": "Legal Landmines",
            "sensitivity_english": "x",
            "sensitivity_local": "x",
            "base_query_english": "q",
            "base_query_local": "q",
            "scenario_english": "english",
            "scenario_local": "   ",
            "hidden_violation": "hv",
        },
        {
            "id": "CC-fr-real",
            "category": "Legal Landmines",
            "sensitivity_english": "x",
            "sensitivity_local": "x",
            "base_query_english": "q",
            "base_query_local": "q",
            "scenario_english": "english",
            "scenario_local": "local",
            "hidden_violation": "hv",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].metadata["row_id"] == "CC-fr-real"


async def test_jailbreak_deduplicates_countries():
    loader = _XLSafetyBenchJailbreakDataset(
        countries=[
            XLSafetyBenchCountry.FRANCE,
            XLSafetyBenchCountry.FRANCE,
            XLSafetyBenchCountry.GERMANY,
        ],
    )
    assert loader._countries == [
        XLSafetyBenchCountry.FRANCE,
        XLSafetyBenchCountry.GERMANY,
    ]
