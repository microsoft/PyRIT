# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

import pytest

from pyrit.cli._cli_args import _argparse_validator, merge_config_scenario_args
from pyrit.setup.configuration_loader import ScenarioConfig


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


class TestMergeConfigScenarioArgs:
    """Tests for the shared CLI/shell config-args merge helper."""

    def test_cli_wins_over_matching_config(self):
        """Config args apply when names match; CLI overrides per-key."""
        config = ScenarioConfig(name="scam", args={"max_turns": 5, "mode": "fast"})
        merged = merge_config_scenario_args(
            config_scenario=config,
            effective_scenario_name="scam",
            cli_args={"max_turns": 10},
        )
        assert merged == {"max_turns": 10, "mode": "fast"}

    def test_warns_and_skips_when_scenario_name_differs(self, caplog):
        """A scenario-name mismatch drops config args and emits a warning."""
        config = ScenarioConfig(name="scam", args={"max_turns": 5})
        with caplog.at_level(logging.WARNING):
            merged = merge_config_scenario_args(
                config_scenario=config,
                effective_scenario_name="other_scenario",
                cli_args={},
            )
        assert merged == {}
        assert "scam" in caplog.text
        assert "other_scenario" in caplog.text

    def test_no_warning_when_config_args_empty(self, caplog):
        """An empty/None args block should not produce a warning even on name mismatch."""
        config = ScenarioConfig(name="scam", args=None)
        with caplog.at_level(logging.WARNING):
            merged = merge_config_scenario_args(
                config_scenario=config,
                effective_scenario_name="other_scenario",
                cli_args={"x": 1},
            )
        assert merged == {"x": 1}
        assert caplog.text == ""

    def test_none_config_returns_cli_args(self):
        """When no scenario block is configured, the helper just passes CLI args through."""
        merged = merge_config_scenario_args(
            config_scenario=None,
            effective_scenario_name="scam",
            cli_args={"max_turns": 10},
        )
        assert merged == {"max_turns": 10}
