# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrit.models.seeds.seed import TemplateParameter, merge_template_parameter_values
from pyrit.models.seeds.seed_prompt import SeedPrompt


def test_name_only_parameters_stay_names() -> None:
    prompt = SeedPrompt(value="Hello {{ objective }}", parameters=["objective"])
    assert prompt.parameters == ["objective"]
    assert prompt.parameter_specs == []


def test_parameter_specs_keep_description_default_and_examples() -> None:
    prompt = SeedPrompt(
        value="Call {{ valid_call }}",
        parameters=[
            {
                "name": "valid_call",
                "description": "A normal allowed call.",
                "default": "refund 25",
                "examples": ["refund 25", "summarize spec"],
            }
        ],
    )
    assert prompt.parameters == ["valid_call"]
    spec = prompt.parameter_specs[0]
    assert spec.name == "valid_call"
    assert spec.description == "A normal allowed call."
    assert spec.default == "refund 25"
    assert spec.examples == ["refund 25", "summarize spec"]


def test_mixed_parameter_list_keeps_names_and_specs() -> None:
    prompt = SeedPrompt(
        value="{{ objective }} {{ valid_call }}",
        parameters=["objective", {"name": "valid_call", "default": "refund 25"}],
    )
    assert prompt.parameters == ["objective", "valid_call"]
    assert prompt.parameter_specs[0] == TemplateParameter(name="objective")
    assert prompt.parameter_specs[1].default == "refund 25"


def test_duplicate_parameter_name_raises() -> None:
    with pytest.raises(ValidationError, match="declared more than once"):
        SeedPrompt(value="{{ valid_call }}", parameters=[{"name": "valid_call"}, {"name": "valid_call"}])


def test_parameter_spec_without_name_raises() -> None:
    with pytest.raises(ValidationError, match="non-empty name"):
        SeedPrompt(value="text", parameters=[{"description": "missing name"}])


def test_merge_uses_defaults_and_replaces_supplied_values() -> None:
    specs = [
        TemplateParameter(name="valid_call", default="refund 25", examples=["refund 25"]),
        TemplateParameter(name="message_format", default="customer message"),
    ]
    merged = merge_template_parameter_values(
        parameter_specs=specs,
        template_values={"valid_call": "summarize spec", "message_format": "", "objective": None},
    )
    assert merged == {"valid_call": "summarize spec", "message_format": "customer message"}


def test_yaml_file_loads_parameter_specs(tmp_path: Path) -> None:
    path = tmp_path / "prompt.yaml"
    path.write_text(
        """value: 'Use {{ valid_call }}'
parameters:
  - name: valid_call
    description: A normal allowed call.
    default: refund 25
    examples:
      - refund 25
""",
        encoding="utf-8",
    )
    prompt = SeedPrompt.from_yaml_file(path)
    assert prompt.parameters == ["valid_call"]
    assert prompt.parameter_specs[0].default == "refund 25"
    assert prompt.parameter_specs[0].examples == ["refund 25"]
    assert "{{ valid_call }}" in prompt.value
