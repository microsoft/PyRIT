# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrit.score import list_assert_judge_presets, load_assert_judge_preset, load_assert_judge_preset_file

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
