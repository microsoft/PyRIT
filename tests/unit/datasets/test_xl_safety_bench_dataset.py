# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset import (
    XLSafetyBenchCountry,
    XLSafetyBenchCulturalCategory,
    XLSafetyBenchJailbreakCategory,
    XLSafetyBenchLanguageMode,
    _XLSafetyBenchCulturalDataset,
    _XLSafetyBenchJailbreakDataset,
    _XLSafetyBenchJailbreakObjectivesDataset,
)
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt


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


async def test_only_logical_datasets_are_registered():
    dataset_names = await SeedDatasetProvider.get_all_dataset_names_async()
    xl_safety_bench_names = {name for name in dataset_names if name.startswith("xl_safety_bench")}

    assert xl_safety_bench_names == {
        "xl_safety_bench_cultural",
        "xl_safety_bench_jailbreak",
        "xl_safety_bench_jailbreak_objectives",
    }


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
    with pytest.raises(ValueError, match="language_mode must be an XLSafetyBenchLanguageMode"):
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
        language_mode=XLSafetyBenchLanguageMode.ENGLISH,
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


# ---------------------------------------------------------------------------
# Jailbreak Objectives loader
# ---------------------------------------------------------------------------


def _patch_objectives_fetch(loader: _XLSafetyBenchJailbreakObjectivesDataset):
    """Patch the objectives loader's URL fetch using per-country jailbreak mock rows."""

    def side_effect(*, source: str, source_type: str, cache: bool) -> list[dict[str, str]]:
        country = source.split("/data/jailbreak/")[1].split("/")[0]
        return _jailbreak_rows(country)

    return patch.object(loader, "_fetch_from_url", side_effect=side_effect)


def test_jailbreak_objectives_dataset_name_and_tags():
    loader = _XLSafetyBenchJailbreakObjectivesDataset()
    assert loader.dataset_name == "xl_safety_bench_jailbreak_objectives"
    # Objectives variant is a derived view over the same upstream CSVs as the prompts
    # variant, so it intentionally OMITS the "default" tag to avoid duplicating those
    # URLs in default-tagged sweeps.
    assert "default" not in _XLSafetyBenchJailbreakObjectivesDataset.tags
    assert "objectives" in _XLSafetyBenchJailbreakObjectivesDataset.tags
    assert "jailbreak" in _XLSafetyBenchJailbreakObjectivesDataset.tags


async def test_jailbreak_objectives_emit_seed_objectives():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(
        countries=[XLSafetyBenchCountry.FRANCE, XLSafetyBenchCountry.JAPAN],
    )

    with _patch_objectives_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    # 2 countries × 2 unique base queries each = 4 objectives.
    assert len(dataset.seeds) == 4
    assert all(isinstance(s, SeedObjective) for s in dataset.seeds)
    countries_seen = {s.metadata["country"] for s in dataset.seeds}
    assert countries_seen == {"france", "japan"}
    # value prefers the local-language base query.
    assert all(s.value.startswith("Local base") for s in dataset.seeds)
    # track field is set to the objectives variant.
    assert all(s.metadata["track"] == "jailbreak_objectives" for s in dataset.seeds)


async def test_jailbreak_objectives_dedupe_repeated_base_queries():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(countries=[XLSafetyBenchCountry.FRANCE])

    # 3 rows but only 2 unique base queries (the first two share base_query_local).
    rows = [
        {
            "id": "JB-fr-1a",
            "category": "Criminal Activities",
            "subcategory_english": "Fraud",
            "subcategory_local": "Fraude",
            "base_query_english": "Same goal, attack A",
            "base_query_local": "Même objectif",
            "attack_prompt": "Attack A",
        },
        {
            "id": "JB-fr-1b",
            "category": "Criminal Activities",
            "subcategory_english": "Fraud",
            "subcategory_local": "Fraude",
            "base_query_english": "Same goal, attack B",
            "base_query_local": "Même objectif",
            "attack_prompt": "Attack B",
        },
        {
            "id": "JB-fr-2",
            "category": "Hate & Discrimination",
            "subcategory_english": "Slur",
            "subcategory_local": "Insulto",
            "base_query_english": "Different goal",
            "base_query_local": "Objectif différent",
            "attack_prompt": "Attack C",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    values = {s.value for s in dataset.seeds}
    assert values == {"Même objectif", "Objectif différent"}


async def test_jailbreak_objectives_category_filter():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(
        countries=[XLSafetyBenchCountry.SPAIN],
        categories=[XLSafetyBenchJailbreakCategory.HATE_AND_DISCRIMINATION],
    )

    with _patch_objectives_fetch(loader):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    seed = dataset.seeds[0]
    assert seed.harm_categories == ["Hate & Discrimination"]
    assert seed.metadata["base_query_english"] == "Base query 2 for spain"
    assert seed.metadata["base_query_local"] == "Local base 2 for spain"
    assert seed.dataset_name == "xl_safety_bench_jailbreak_objectives"


async def test_jailbreak_objectives_falls_back_to_english_when_local_blank():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(countries=[XLSafetyBenchCountry.FRANCE])
    rows = [
        {
            "id": "JB-fr-only-en",
            "category": "Criminal Activities",
            "subcategory_english": "X",
            "subcategory_local": "Y",
            "base_query_english": "English-only fallback goal",
            "base_query_local": "   ",
            "attack_prompt": "Attack ignored",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].value == "English-only fallback goal"


async def test_jailbreak_objectives_raises_when_filter_matches_nothing():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(
        countries=[XLSafetyBenchCountry.FRANCE],
        categories=[XLSafetyBenchJailbreakCategory.POLITICAL_AND_MISINFORMATION],
    )

    with _patch_objectives_fetch(loader):
        with pytest.raises(ValueError, match="No XL-SafetyBench jailbreak objectives"):
            await loader.fetch_dataset_async()


# ---------------------------------------------------------------------------
# BOM handling — HuggingFace ships the CSVs with a leading UTF-8 BOM so the
# first column arrives as "\ufeffid". The loader must strip it so row_id and
# the per-seed `name` aren't silently empty across all 4,500 prompts.
# ---------------------------------------------------------------------------


async def test_jailbreak_strips_bom_from_id_column():
    loader = _XLSafetyBenchJailbreakDataset(countries=[XLSafetyBenchCountry.FRANCE])
    bom_rows = [
        {
            "\ufeffid": "JB-fr-bom-1",
            "category": "Criminal Activities",
            "subcategory_english": "Fraud",
            "subcategory_local": "Fraude",
            "base_query_english": "bq1",
            "base_query_local": "bql1",
            "attack_prompt": "Attack one",
        },
        {
            "\ufeffid": "JB-fr-bom-2",
            "category": "Hate & Discrimination",
            "subcategory_english": "Slur",
            "subcategory_local": "Insulto",
            "base_query_english": "bq2",
            "base_query_local": "bql2",
            "attack_prompt": "Attack two",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=bom_rows):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    row_ids = {s.metadata["row_id"] for s in dataset.seeds}
    assert row_ids == {"JB-fr-bom-1", "JB-fr-bom-2"}
    names = {s.name for s in dataset.seeds}
    assert names == {"XL-SafetyBench Jailbreak JB-fr-bom-1", "XL-SafetyBench Jailbreak JB-fr-bom-2"}


async def test_cultural_strips_bom_from_id_column():
    loader = _XLSafetyBenchCulturalDataset(countries=[XLSafetyBenchCountry.FRANCE])
    bom_rows = [
        {
            "\ufeffid": "CC-fr-bom-1",
            "category": "Symbolic Taboos & Gift-Giving",
            "sensitivity_english": "Gift taboo",
            "sensitivity_local": "Tabou cadeau",
            "base_query_english": "Suggest a gift",
            "base_query_local": "Suggère un cadeau",
            "scenario_english": "English scenario",
            "scenario_local": "Local scenario",
            "hidden_violation": "Taboo gift",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=bom_rows):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].metadata["row_id"] == "CC-fr-bom-1"
    assert dataset.seeds[0].name == "XL-SafetyBench Cultural CC-fr-bom-1"


async def test_jailbreak_objectives_strips_bom_from_id_column():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(countries=[XLSafetyBenchCountry.FRANCE])
    bom_rows = [
        {
            "\ufeffid": "JB-fr-bom-obj-1",
            "category": "Criminal Activities",
            "subcategory_english": "Fraud",
            "subcategory_local": "Fraude",
            "base_query_english": "Goal english",
            "base_query_local": "Goal local",
            "attack_prompt": "Attack",
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=bom_rows):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].metadata["row_id"] == "JB-fr-bom-obj-1"
    assert dataset.seeds[0].name == "XL-SafetyBench Jailbreak Objective france JB-fr-bom-obj-1"


# ---------------------------------------------------------------------------
# CSV schema drift and short-row defense — `_fetch_from_url` returns whatever
# `csv.DictReader` parses, which means: (1) if HuggingFace renames or drops a
# column the loader silently emits empty seeds, and (2) short rows yield None
# cell values that would `str(None)`-stringify into the literal "None". Both
# regressions need to be guarded.
# ---------------------------------------------------------------------------


async def test_jailbreak_raises_when_required_column_missing():
    loader = _XLSafetyBenchJailbreakDataset(countries=[XLSafetyBenchCountry.FRANCE])
    # `attack_prompt` is the column the loader actually uses to build prompt values.
    bad_rows = [
        {
            "id": "JB-fr-bad-1",
            "category": "Criminal Activities",
            "base_query_english": "bq",
            "base_query_local": "bql",
            # NOTE: no "attack_prompt" key — simulates upstream column rename.
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=bad_rows):
        with pytest.raises(ValueError, match="attack_prompt"):
            await loader.fetch_dataset_async()


async def test_jailbreak_objectives_raises_when_required_column_missing():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(countries=[XLSafetyBenchCountry.FRANCE])
    bad_rows = [
        {
            "id": "JB-fr-bad-obj-1",
            "category": "Criminal Activities",
            # NOTE: no base_query_* columns.
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=bad_rows):
        with pytest.raises(ValueError, match="base_query_local"):
            await loader.fetch_dataset_async()


async def test_cultural_raises_when_required_column_missing():
    loader = _XLSafetyBenchCulturalDataset(countries=[XLSafetyBenchCountry.FRANCE])
    bad_rows = [
        {
            "id": "CC-fr-bad-1",
            "category": "Symbolic Taboos & Gift-Giving",
            "sensitivity_english": "se",
            "sensitivity_local": "sl",
            "base_query_english": "bq",
            "base_query_local": "bql",
            "scenario_english": "ses",
            # NOTE: no "scenario_local" and no "hidden_violation".
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=bad_rows):
        with pytest.raises(ValueError, match="scenario_local|hidden_violation"):
            await loader.fetch_dataset_async()


async def test_jailbreak_handles_none_cell_values_from_short_rows():
    loader = _XLSafetyBenchJailbreakDataset(countries=[XLSafetyBenchCountry.FRANCE])
    # Required columns are present but several optional ones come back as None
    # (the way csv.DictReader populates missing trailing cells for short rows).
    rows_with_none = [
        {
            "id": "JB-fr-none-1",
            "category": "Criminal Activities",
            "attack_prompt": "Attack one",
            "base_query_english": "bq1",
            "base_query_local": "bql1",
            "subcategory_english": None,
            "subcategory_local": None,
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows_with_none):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    seed = dataset.seeds[0]
    # The literal string "None" must not propagate into metadata.
    assert seed.metadata["subcategory_english"] == ""
    assert seed.metadata["subcategory_local"] == ""
    assert seed.value == "Attack one"


async def test_jailbreak_objectives_handles_none_cell_values_from_short_rows():
    loader = _XLSafetyBenchJailbreakObjectivesDataset(countries=[XLSafetyBenchCountry.FRANCE])
    rows_with_none = [
        {
            "id": "JB-fr-none-obj-1",
            "category": "Criminal Activities",
            "base_query_english": "Goal english",
            "base_query_local": "Goal local",
            "subcategory_english": None,
            "subcategory_local": None,
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows_with_none):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    seed = dataset.seeds[0]
    assert seed.metadata["subcategory_english"] == ""
    assert seed.metadata["subcategory_local"] == ""
    assert seed.value == "Goal local"


async def test_cultural_handles_none_cell_values_from_short_rows():
    loader = _XLSafetyBenchCulturalDataset(countries=[XLSafetyBenchCountry.FRANCE])
    rows_with_none = [
        {
            "id": "CC-fr-none-1",
            "category": "Symbolic Taboos & Gift-Giving",
            "scenario_english": "English scenario",
            "scenario_local": "Local scenario",
            "hidden_violation": "Taboo gift",
            "sensitivity_english": None,
            "sensitivity_local": None,
            "base_query_english": None,
            "base_query_local": None,
        },
    ]

    with patch.object(loader, "_fetch_from_url", return_value=rows_with_none):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    seed = dataset.seeds[0]
    assert seed.metadata["sensitivity_english"] == ""
    assert seed.metadata["sensitivity_local"] == ""
    assert seed.metadata["base_query_english"] == ""
    assert seed.metadata["base_query_local"] == ""
    assert seed.value == "Local scenario"
