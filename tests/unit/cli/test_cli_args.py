# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.cli._cli_args import _argparse_validator


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
