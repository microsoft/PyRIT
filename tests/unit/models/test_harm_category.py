# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the harm_category taxonomy and standardization utilities."""

import logging

import pytest

from pyrit.models.harm_category import (
    _HARM_CATEGORY_DEFINITIONS,
    HARM_CATEGORY_TAXONOMY_VERSION,
    HarmCategory,
    HarmCategoryPillar,
    standardize_harm_categories,
)


def test_taxonomy_version_read_from_yaml() -> None:
    assert HARM_CATEGORY_TAXONOMY_VERSION == "v1.0.0"


def test_str_returns_display_value() -> None:
    # StrEnum (and the <3.11 backport) must render the value, not "HarmCategory.HATE_SPEECH".
    assert str(HarmCategory.HATE_SPEECH) == "Hate Speech"
    assert f"{HarmCategory.VIOLENT_CONTENT}" == "Graphic Violence and Gore"


@pytest.mark.parametrize("empty", [None, "", [], ["", None]])
def test_standardize_empty_input_returns_empty_list(empty) -> None:
    assert standardize_harm_categories(empty) == []


def test_standardize_single_string_input() -> None:
    assert standardize_harm_categories("violence") == ["VIOLENT_CONTENT"]


def test_standardize_strips_whitespace() -> None:
    assert standardize_harm_categories([" violence ", "  "]) == ["VIOLENT_CONTENT"]


def test_standardize_deduplicates_many_to_one() -> None:
    # "violence" and "physical harm" both canonicalize to VIOLENT_CONTENT.
    assert standardize_harm_categories(["violence", "physical harm"]) == ["VIOLENT_CONTENT"]


def test_standardize_deduplicates_overlapping_one_to_many() -> None:
    # Both map to [REPRESENTATIONAL, HATE_SPEECH]; the overlap must not repeat.
    assert standardize_harm_categories(["racism", "sexism"]) == ["REPRESENTATIONAL", "HATE_SPEECH"]


def test_canonical_name_resolves() -> None:
    assert standardize_harm_categories(["VIOLENT_CONTENT"]) == ["VIOLENT_CONTENT"]


def test_canonical_display_value_resolves() -> None:
    assert standardize_harm_categories(["Graphic Violence and Gore"]) == ["VIOLENT_CONTENT"]


def test_unknown_category_falls_back_to_other_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = standardize_harm_categories(["nonsense-label-xyz"])
    assert result == ["OTHER"]
    assert any("nonsense-label-xyz" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("hate", ["HATE_SPEECH", "REPRESENTATIONAL"]),
        ("adult content", ["SEXUAL_CONTENT"]),
        ("cybercrime", ["COORDINATION_HARM", "MALWARE"]),
        ("defamation", ["REPUTATIONAL_DAMAGE"]),
        ("disinformation", ["INFO_INTEGRITY"]),
        ("fraud", ["DECEPTION", "SCAMS"]),
        ("government decision-making", ["HIGH_RISK_GOVERNMENT"]),
        ("illegal activity", ["COORDINATION_HARM"]),
        ("malware/hacking", ["MALWARE"]),
        ("privacy", ["PPI"]),
        ("theft", ["COORDINATION_HARM"]),
        ("child abuse", ["CHILD_LEAKAGE", "GROOMING", "SEXUAL_CONTENT"]),
    ],
)
def test_promoted_cross_dataset_aliases(raw, expected) -> None:
    assert standardize_harm_categories([raw]) == expected


def test_alias_overrides_case_insensitive_key() -> None:
    # Override key differs in case from the raw label; both should still match.
    result = standardize_harm_categories(
        ["custom label"],
        alias_overrides={"Custom Label": [HarmCategory.CBRN]},
    )
    assert result == ["CBRN"]


def test_alias_overrides_case_insensitive_value_side() -> None:
    result = standardize_harm_categories(
        ["CUSTOM LABEL"],
        alias_overrides={"custom label": [HarmCategory.CBRN]},
    )
    assert result == ["CBRN"]


def test_alias_overrides_accepts_single_enum_value() -> None:
    # visual_leak_bench-style overrides pass a bare HarmCategory, not a list.
    result = standardize_harm_categories(
        ["pii_leakage"],
        alias_overrides={"pii_leakage": HarmCategory.PPI},
    )
    assert result == ["PPI"]


def test_alias_overrides_beat_builtin_alias() -> None:
    # "violence" is a built-in alias for VIOLENT_CONTENT, but an override wins.
    result = standardize_harm_categories(
        ["violence"],
        alias_overrides={"violence": [HarmCategory.CBRN]},
    )
    assert result == ["CBRN"]


def test_canonical_match_beats_alias_override() -> None:
    # A canonical name/value is resolved before overrides are consulted, so an
    # override keyed on a canonical term cannot shadow it.
    result = standardize_harm_categories(
        ["VIOLENT_CONTENT"],
        alias_overrides={"violent_content": [HarmCategory.CBRN]},
    )
    assert result == ["VIOLENT_CONTENT"]


def test_parse_returns_first_of_many() -> None:
    assert HarmCategory.parse("hate") == HarmCategory.HATE_SPEECH


def test_parse_many_returns_all() -> None:
    assert HarmCategory.parse_many("hate") == [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL]


def test_parse_unknown_returns_other() -> None:
    assert HarmCategory.parse("totally-unknown") == HarmCategory.OTHER


def test_every_category_has_a_definition() -> None:
    missing = [member.name for member in HarmCategory if not _HARM_CATEGORY_DEFINITIONS.get(member.name, "").strip()]
    assert not missing, f"HarmCategory members missing a definition: {missing}"


def test_no_stray_definition_keys() -> None:
    valid_names = {member.name for member in HarmCategory}
    stray = sorted(set(_HARM_CATEGORY_DEFINITIONS) - valid_names)
    assert not stray, f"Definitions reference unknown HarmCategory names: {stray}"


def test_get_definition_returns_defined_text() -> None:
    assert HarmCategory.get_definition(HarmCategory.MALWARE) == "Creating or distributing malicious software."


def test_pillar_count_matches_taxonomy() -> None:
    assert len(list(HarmCategoryPillar)) == 22


def test_pillar_str_returns_display_value() -> None:
    assert str(HarmCategoryPillar.CHILD_SAFETY) == "Child Safety"
    assert f"{HarmCategoryPillar.IP}" == "Intellectual Property"


def test_every_pillar_has_at_least_one_category() -> None:
    empty = [pillar.name for pillar in HarmCategoryPillar if not pillar.categories()]
    assert not empty, f"Pillars with no categories: {empty}"


def test_pillar_categories_are_all_harm_categories() -> None:
    for pillar in HarmCategoryPillar:
        for category in pillar.categories():
            assert isinstance(category, HarmCategory)


def test_every_category_except_other_belongs_to_a_pillar() -> None:
    orphans = [c.name for c in HarmCategory if c is not HarmCategory.OTHER and not c.pillars()]
    assert not orphans, f"HarmCategory members not assigned to any pillar: {orphans}"


def test_other_belongs_to_no_pillar() -> None:
    assert HarmCategory.OTHER.pillars() == []


def test_pillar_and_category_are_inverse_consistent() -> None:
    for pillar in HarmCategoryPillar:
        for category in pillar.categories():
            assert pillar in category.pillars()
    for category in HarmCategory:
        for pillar in category.pillars():
            assert category in pillar.categories()


def test_category_can_belong_to_multiple_pillars() -> None:
    harassment_pillars = set(HarmCategory.HARASSMENT.pillars())
    assert {HarmCategoryPillar.CHILD_SAFETY, HarmCategoryPillar.HARMFUL_CONTENT} <= harassment_pillars

    suicide_pillars = set(HarmCategory.SUICIDE.pillars())
    assert {HarmCategoryPillar.CHILD_SAFETY, HarmCategoryPillar.SELF_INJURY} <= suicide_pillars
