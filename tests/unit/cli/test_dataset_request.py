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
        "--dataset-fetch always",
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
