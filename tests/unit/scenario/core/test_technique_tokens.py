# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import assert_type

from pyrit.scenario import ScenarioTechnique
from pyrit.scenario.core import TechniqueTokenResolution, parse_technique_token, resolve_technique_tokens


class _TypedTechnique(ScenarioTechnique):
    ALL = ("all", {"all"})
    First = ("first", set())
    Second = ("second", set())


def test_parse_technique_token_preserves_ordered_modifiers() -> None:
    parsed = parse_technique_token("first:converter.one:converter.two")

    assert parsed.base_name == "first"
    assert parsed.modifiers == ("converter.one", "converter.two")


def test_resolve_technique_tokens_preserves_concrete_technique_type() -> None:
    resolution = resolve_technique_tokens(
        tokens=["first", "second"],
        technique_class=_TypedTechnique,
        scenario_name="typed-test",
    )

    assert_type(resolution, TechniqueTokenResolution[_TypedTechnique])
    assert_type(resolution.techniques, list[_TypedTechnique])
    assert resolution.techniques == [_TypedTechnique.First, _TypedTechnique.Second]
    assert resolution.technique_converters == {}
    assert resolution.issues == []


def test_resolve_technique_tokens_reports_invalid_techniques_without_changing_types() -> None:
    resolution = resolve_technique_tokens(
        tokens=["missing", "first"],
        technique_class=_TypedTechnique,
        scenario_name="typed-test",
    )

    assert_type(resolution.techniques, list[_TypedTechnique])
    assert resolution.techniques == [_TypedTechnique.First]
    assert [issue.kind for issue in resolution.issues] == ["unknown_technique"]
    assert resolution.issues[0].name == "missing"
