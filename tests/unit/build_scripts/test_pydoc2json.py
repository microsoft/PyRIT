# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise source extraction without importing the documented package."""

import logging
from pathlib import Path

import griffe
import pytest

from build_scripts.gen_api_md import render_class
from build_scripts.pydoc2json import class_to_dict, function_to_dict


def test_overloaded_docs_preserve_runtime_signature_and_validate_names(caplog: pytest.LogCaptureFixture) -> None:
    module = griffe.visit(
        "sample",
        filepath=Path("sample.py"),
        code='''
from typing import overload

@overload
def execute(*, objective: str) -> str: ...
@overload
def execute(*, context: int) -> str: ...
def execute(**kwargs) -> str:
    """Execute either public contract.

    Args:
        objective (str): The objective.
        context (int): The context.
        typo (str): An invalid parameter.
    """
''',
    )
    with caplog.at_level(logging.WARNING):
        result = function_to_dict(module["execute"])
    assert [param["name"] for param in result["signature"]] == ["kwargs"]
    assert [param["name"] for param in result["docstring"]["params"]] == ["objective", "context", "typo"]
    assert "Parameter 'typo' does not appear" in caplog.text
    assert "Parameter 'objective' does not appear" not in caplog.text
    assert "Parameter 'context' does not appear" not in caplog.text
    assert [param.name for param in module["execute"].parameters] == ["kwargs"]


def test_attributes_and_type_parameters_survive_extraction_and_rendering() -> None:
    module = griffe.visit(
        "sample",
        filepath=Path("sample.py"),
        code='''
class Result:
    """A result.

    Attributes:
        value (str): The stored value.

    Further details after the field documentation.

    Type Parameters:
        T: The result type.
    """
    value: str
''',
    )
    result = class_to_dict(module["Result"])
    assert result["docstring"]["attributes"][0]["name"] == "value"
    assert result["docstring"]["type_params"][0]["name"] == "T"
    assert "A result." in result["docstring"]["text"]
    assert "Further details" in result["docstring"]["text"]
    markdown = render_class(result, module="sample")
    assert "**Attributes:**" in markdown
    assert "| `value` | `str` |" in markdown
    assert "**Type Parameters:**" in markdown
    assert "The result type." in markdown


@pytest.mark.parametrize(
    ("module_name", "class_name", "field"),
    [
        ("pyrit.memory.memory_models", "PromptMemoryEntry", "id"),
        ("pyrit.memory.memory_models", "EmbeddingDataEntry", "embedding"),
        ("pyrit.memory.memory_models", "SeedEntry", "value"),
        ("pyrit.memory.memory_models", "AttackResultEntry", "objective"),
        ("pyrit.score.scorer_evaluation.human_labeled_dataset", "HumanLabeledEntry", "conversation"),
        ("pyrit.score.scorer_evaluation.scorer_evaluator", "ScorerEvalDatasetFiles", "result_file"),
    ],
)
def test_data_fields_are_rendered_as_attributes(
    module_name: str, class_name: str, field: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = Path(__file__).resolve().parents[3].joinpath(*module_name.split(".")).with_suffix(".py")
    module = griffe.visit(module_name, filepath=path, code=path.read_text(encoding="utf-8"))
    result = class_to_dict(module[class_name])
    assert field in {item["name"] for item in result["docstring"]["attributes"]}
    assert "params" not in result["docstring"]
    assert "does not appear in the function signature" not in caplog.text
