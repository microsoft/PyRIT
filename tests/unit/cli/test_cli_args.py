# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.cli._cli_args import _argparse_validator, parse_run_arguments
from pyrit.models import Parameter


def _sp(*, name: str, description: str = "", param_type: str = "str") -> Parameter:
    """Build a real Parameter from Summary-style kwargs (param_type as a string)."""
    return Parameter.model_validate(
        {
            "name": name,
            "description": description,
            "default": None,
            "type_name": param_type,
            "choices": None,
            "is_list": False,
        }
    )


def test_argparse_validator_no_params_raises():
    """Validator with zero parameters should raise ValueError."""
    no_param_func = eval("lambda: None")
    with pytest.raises(ValueError, match="must have at least one parameter"):
        _argparse_validator(no_param_func)


def test_argparse_validator_wraps_keyword_only():
    """Validator with keyword-only param should work via positional call."""

    def validate_name(*, name: str) -> str:
        if not name:
            raise ValueError("name is required")
        return name.upper()

    wrapped = _argparse_validator(validate_name)
    assert wrapped("hello") == "HELLO"


def test_parse_run_arguments_skips_scenario_params_colliding_with_builtins():
    """
    Scenario ``supported_parameters`` include framework common params (e.g. memory_labels)
    whose flags match built-ins. Those must be silently dropped, not raise, so ``run`` works.
    """
    declared_params = [
        _sp(name="memory_labels", description="Common framework param."),
        _sp(name="max_concurrency", description="Common framework param.", param_type="int"),
        _sp(name="max_turns", description="Scenario custom param.", param_type="int"),
    ]

    result = parse_run_arguments(
        args_string="airt.scam --target openai_chat --max-turns 3",
        declared_params=declared_params,
    )

    assert result["scenario_name"] == "airt.scam"
    assert result["target"] == "openai_chat"
    # The dropped common params keep their built-in result keys (not scenario__-prefixed).
    assert result["scenario__max_turns"] == 3
    assert "scenario__memory_labels" not in result
    assert "scenario__max_concurrency" not in result


def test_parse_run_arguments_first_wins_on_scenario_vs_scenario_collision():
    """Two scenario params normalizing to the same CLI flag: first wins, second is dropped (parity with pyrit_scan)."""
    declared_params = [
        _sp(name="max_turns", description="First."),
        _sp(name="max-turns", description="Normalizes to the same flag."),
    ]

    result = parse_run_arguments(
        args_string="airt.scam --max-turns 4",
        declared_params=declared_params,
    )

    # The first declaration owns the flag; only one scenario__ key is produced.
    assert result["scenario__max_turns"] == "4"
    assert "scenario__max-turns" not in result
