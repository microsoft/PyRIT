# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest

from pyrit.models import AssertJudgeDimension, AssertJudgePreset
from pyrit.models.score.assert_judge import AssertJudgeConfig

_RUBRIC = "true = The event occurred.\nfalse = The event did not occur."


def test_dimension_is_a_boolean_flag() -> None:
    dimension = AssertJudgeDimension(name="harm_actionability", description="Harmful?", rubric=_RUBRIC)
    assert dimension.model_dump() == {"name": "harm_actionability", "description": "Harmful?", "rubric": _RUBRIC}


@pytest.mark.parametrize(
    "option",
    [{"scale": {"type": "ordinal", "values": {1: "Bad", 2: "Good"}}}, {"allow_not_applicable": True}],
)
def test_dimension_rejects_unsupported_assert_options(option: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="not supported. PyRIT judge dimensions are boolean bad-event flags"):
        AssertJudgeDimension.model_validate({"name": "quality", "description": "d", "rubric": _RUBRIC, **option})


@pytest.mark.parametrize("name", AssertJudgeDimension.BUILT_IN_NAMES)
def test_dimension_rejects_built_in_names(name: str) -> None:
    with pytest.raises(ValueError, match="built-in ASSERT judge dimension"):
        AssertJudgeDimension(name=name, description="d", rubric=_RUBRIC)


@pytest.mark.parametrize("name", ["Harm", "harm-actionability", "1harm", ""])
def test_dimension_rejects_non_identifier_names(name: str) -> None:
    with pytest.raises(ValueError):
        AssertJudgeDimension(name=name, description="d", rubric=_RUBRIC)


@pytest.mark.parametrize("field", ["description", "rubric"])
def test_dimension_requires_description_and_rubric(field: str) -> None:
    values = {"name": "harm", "description": "d", "rubric": _RUBRIC, field: "  "}
    with pytest.raises(ValueError):
        AssertJudgeDimension(**values)


def test_preset_accepts_assert_mapping_form() -> None:
    preset = AssertJudgePreset.model_validate(
        {
            "kind": "judge_preset",
            "name": "budget",
            "dimensions": {
                "budget_overrun": {"description": "Over budget?", "rubric": _RUBRIC},
                "verification_skip": {"description": "Skipped checks?", "rubric": _RUBRIC},
            },
        }
    )
    assert [dimension.name for dimension in preset.dimensions] == ["budget_overrun", "verification_skip"]


def test_preset_from_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    path.write_text(
        "kind: judge_preset\nname: budget\ndimensions:\n  budget_overrun:\n    description: Over?\n"
        "    rubric: |-\n      true = Over budget.\n      false = Within budget.\n",
        encoding="utf-8",
    )
    preset = AssertJudgePreset.from_yaml_file(path)
    assert preset.dimensions[0].rubric == "true = Over budget.\nfalse = Within budget."


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- not a mapping\n", "YAML mapping"),
        ("kind: judge_preset\nname: empty\n", "at least one dimension"),
        ("kind: behavior\nname: wrong\nbuiltin_dimensions: [overrefusal]\n", "kind: Input should be"),
        ("kind: judge_preset\nname: unknown\nbuiltin_dimensions: [refusal]\n", "Unknown built-in"),
        (
            (
                "kind: judge_preset\nname: graded\ndimensions:\n  quality:\n    description: Good?\n"
                "    rubric: Grade it.\n    scale:\n      type: ordinal\n      values: {1: Bad, 2: Good}\n"
            ),
            "dimensions.0: Value error, ASSERT judge dimension option\\(s\\) scale are not supported",
        ),
    ],
)
def test_preset_from_yaml_file_rejects_invalid_presets(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        AssertJudgePreset.from_yaml_file(path)


def test_preset_rejects_duplicate_dimension_names() -> None:
    dimension = {"name": "harm", "description": "d", "rubric": _RUBRIC}
    with pytest.raises(ValueError, match="unique"):
        AssertJudgePreset(name="dupes", dimensions=[dimension, dimension])


def test_config_defaults_match_assert() -> None:
    config = AssertJudgeConfig()
    assert config.presets == ()
    assert config.samples == 1
    assert config.objective_dimension == "policy_violation"


@pytest.mark.parametrize(
    "values",
    [
        {"samples": 0},
        {"samples": True},
        {"presets": ("grounding", "grounding")},
        {"presets": ("../safety-core",)},
        {"presets": ("safety-core.yaml",)},
        {"preset_files": ("a.yaml", "a.yaml")},
        {"disabled_dimensions": ("overrefusal", "overrefusal")},
        {"dimensions": [{"name": "a", "description": "d", "rubric": "r"}] * 2},
    ],
)
def test_config_rejects_invalid_settings(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AssertJudgeConfig.model_validate(values)
