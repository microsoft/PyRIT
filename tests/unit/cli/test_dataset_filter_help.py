# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Guard that the CLI's advertised dataset-filter keys stay in sync with the resolver."""

from pyrit.cli._cli_args import _ADVERTISED_DATASET_FILTER_KEYS, ARG_HELP
from pyrit.scenario.core.dataset_configuration import DATASET_FILTERS


def test_cli_advertised_filters_match_dataset_configuration() -> None:
    # The static CLI list must equal the exact filter kwargs the resolver accepts.
    assert set(_ADVERTISED_DATASET_FILTER_KEYS) == set(DATASET_FILTERS)


def test_help_text_lists_every_advertised_key() -> None:
    help_text = ARG_HELP["dataset_parameters"]
    for key in _ADVERTISED_DATASET_FILTER_KEYS:
        assert key in help_text
