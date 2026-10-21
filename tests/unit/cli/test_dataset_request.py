# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scanner and shell share strict edit-only dataset parsing."""

from contextlib import nullcontext

import pytest

from pyrit.cli._cli_args import build_dataset_request_fields, parse_run_arguments
from pyrit.cli.pyrit_scan import _build_run_request, parse_args


@pytest.mark.parametrize(
    "flags",
    [
        "--datasets harmbench --dataset-names other",
        "--max-per-dataset 2 --max-dataset-size 4",
        "--max-total all --dataset-names harmbench",
        "--dataset-fetch never --max-dataset-size default",
    ],
)
def test_scanner_and_shell_reject_mixed_dataset_flags(flags: str) -> None:
    parsed = parse_args(["run", "test", "--target", "target", *flags.split()])
    with pytest.raises(ValueError, match="Do not mix"):
        _build_run_request(parsed_args=parsed, scenario_name="test")
    with pytest.raises(ValueError, match="Do not mix"):
        build_dataset_request_fields(parse_run_arguments(args_string="test " + flags))


@pytest.mark.parametrize(
    "flags",
    [
        "--datasets name:0",
        "--datasets name:4:all",
        "--datasets name name",
        "--dataset-fetch sometimes",
        "--max-total -1",
    ],
)
def test_shell_rejects_malformed_datasets(flags: str) -> None:
    with pytest.raises(ValueError):
        build_dataset_request_fields(parse_run_arguments(args_string="test " + flags))


@pytest.mark.parametrize("source_flags", ["--datasets harmbench:all", "--dataset-names harmbench"])
def test_common_filters_work_with_either_form(source_flags: str) -> None:
    flags = source_flags + " --dataset-filters data_types=text,image_path"
    with pytest.warns(DeprecationWarning) if "--dataset-names" in flags else nullcontext():
        fields = build_dataset_request_fields(parse_run_arguments(args_string="test " + flags))
    if "datasets" in fields:
        assert fields["datasets"].filters == {"data_types": ["text", "image_path"]}
    else:
        assert fields["dataset_filters"] == {"data_types": ["text", "image_path"]}


@pytest.mark.parametrize("policy", ["never", "if_missing", "always"])
def test_scanner_and_shell_accept_dataset_fetch_policy(policy: str) -> None:
    flags = f"--datasets harmbench --dataset-fetch {policy}"
    parsed = parse_args(["run", "test", "--target", "target", *flags.split()])
    scanner = _build_run_request(parsed_args=parsed, scenario_name="test")
    shell = build_dataset_request_fields(parse_run_arguments(args_string="test " + flags))
    assert scanner.datasets == shell["datasets"]
    assert scanner.datasets is not None
    assert scanner.datasets.fetch == policy


def test_scanner_lists_dataset_fetch_choices(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        parse_args(["run", "test", "--dataset-fetch", "sometimes"])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "invalid choice" in message
    assert "sometimes" in message
    for policy in ("never", "if_missing", "always"):
        assert policy in message


def test_scanner_fetch_help_preserves_scenario_default(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        parse_args(["run", "--help"])
    assert error.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--dataset-fetch {never,if_missing,always}" in help_text
    assert "omission keeps scenario settings" in help_text
