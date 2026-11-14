# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrit.models import AssertJudgeDimension
from pyrit.models.score.assert_judge import AssertJudgeConfig
from pyrit.score import (
    AssertScoringDimension,
    list_assert_judge_presets,
    load_assert_judge_preset,
    load_assert_judge_preset_file,
)
from pyrit.score.assert_judge_presets import get_assert_dimension_name, resolve_assert_judge_dimensions

_UPSTREAM_PRESETS = {
    "alignment": ["sycophancy", "verification_skip"],
    "communication": ["communication_quality"],
    "grounding": ["hallucination", "attribution_error"],
    "instruction-following": ["constraint_violation", "partial_completion"],
    "multi-turn": ["context_loss", "goal_drift"],
    "operational": ["workflow_violation", "escalation_judgment"],
    "policy-adherence": ["policy_adherence"],
    "robustness": ["prompt_injection", "boundary_violation"],
    "safety-core": [],
    "safety-extended": ["harm_actionability", "pii_leakage"],
    "tool-use": ["wrong_tool", "incorrect_arguments", "missing_tool_call", "redundant_tool_call"],
}


def _names(config: AssertJudgeConfig) -> list[str]:
    return [get_assert_dimension_name(dimension) for dimension in resolve_assert_judge_dimensions(config)]


def _dimension(name: str, *, description: str = "Did it happen?") -> AssertJudgeDimension:
    return AssertJudgeDimension(name=name, description=description, rubric="true = yes\nfalse = no")


def test_shipped_presets_match_assert_library() -> None:
    presets = list_assert_judge_presets()
    assert {name: [dimension.name for dimension in preset.dimensions] for name, preset in presets.items()} == (
        _UPSTREAM_PRESETS
    )
    assert presets["safety-core"].builtin_dimensions == ("policy_violation", "overrefusal")
    assert all(dimension.rubric for preset in presets.values() for dimension in preset.dimensions)


def test_load_preset_by_name_and_file(tmp_path: Path) -> None:
    assert load_assert_judge_preset("tool-use").name == "tool-use"
    path = tmp_path / "mine.yml"
    path.write_text(
        "kind: judge_preset\nname: mine\ndimensions:\n  leak:\n    description: Leak?\n    rubric: true = leak\n",
        encoding="utf-8",
    )
    assert load_assert_judge_preset_file(str(path)).dimensions[0].name == "leak"
    assert load_assert_judge_preset_file(path).name == "mine"


@pytest.mark.parametrize(
    "name",
    ["no-such-preset", "safety-core.yaml", "../judge_presets/safety-core", "../../../../pyproject", "/etc/passwd"],
)
def test_load_preset_accepts_only_built_in_names(name: str) -> None:
    with pytest.raises(ValueError, match="Unknown judge preset. Use one of: alignment"):
        load_assert_judge_preset(name)


def test_load_preset_file_rejects_missing_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_assert_judge_preset_file(tmp_path / "missing.yaml")


def test_invalid_preset_file_errors_preserve_validation_details(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(
        "kind: judge_preset\nname: test\ndimensions:\n  quality:\n    description: 123\n    rubric: Good?\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid judge preset file") as error:
        load_assert_judge_preset_file(path)
    assert isinstance(error.value.__cause__, ValidationError)
    assert any(item["input"] == 123 for item in error.value.__cause__.errors())
    assert "123" in str(error.value)


def test_default_config_resolves_to_built_ins() -> None:
    assert resolve_assert_judge_dimensions(AssertJudgeConfig()) == tuple(AssertScoringDimension)


def test_presets_files_and_inline_dimensions_merge_in_order(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text(
        "kind: judge_preset\nname: mine\ndimensions:\n  pii_leakage:\n    description: File wins.\n"
        "    rubric: true = leak\n  hallucination:\n    description: File.\n    rubric: true = made up\n",
        encoding="utf-8",
    )
    override = _dimension("hallucination", description="Inline definition wins.")
    config = AssertJudgeConfig(
        presets=("safety-core", "grounding", "safety-extended"), preset_files=(path,), dimensions=(override,)
    )
    resolved = resolve_assert_judge_dimensions(config)
    assert [get_assert_dimension_name(dimension) for dimension in resolved] == [
        "policy_violation",
        "overrefusal",
        "hallucination",
        "attribution_error",
        "harm_actionability",
        "pii_leakage",
    ]
    assert resolved[2] == override
    assert resolved[5].description == "File wins."


def test_disabled_dimensions_apply_after_presets() -> None:
    config = AssertJudgeConfig(
        presets=("safety-extended",),
        disabled_dimensions=("overrefusal", "pii_leakage"),
    )
    assert _names(config) == ["policy_violation", "harm_actionability"]


def test_custom_objective_can_replace_policy_violation() -> None:
    config = AssertJudgeConfig(
        dimensions=(_dimension("guardrail_policy_violation"),),
        disabled_dimensions=("policy_violation",),
        objective_dimension="guardrail_policy_violation",
    )
    assert _names(config) == ["overrefusal", "guardrail_policy_violation"]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (AssertJudgeConfig(disabled_dimensions=("hallucination",)), "Unknown disabled"),
        (
            AssertJudgeConfig(disabled_dimensions=("policy_violation", "overrefusal"), objective_dimension="x"),
            "at least one dimension",
        ),
        (AssertJudgeConfig(disabled_dimensions=("policy_violation",)), "not an enabled"),
        (AssertJudgeConfig(presets=("nope",)), "Unknown judge preset"),
        (AssertJudgeConfig(preset_files=(Path("missing.yaml"),)), "not found"),
    ],
)
def test_invalid_configs_are_rejected(config: AssertJudgeConfig, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_assert_judge_dimensions(config)
