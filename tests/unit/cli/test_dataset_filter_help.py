# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Guard that the CLI's advertised dataset-filter keys stay in sync with the resolver."""

from pyrit.cli._cli_args import _ADVERTISED_DATASET_FILTER_KEYS
from pyrit.scenario.core.dataset_configuration import DATASET_FILTERS


def test_cli_advertised_filters_match_dataset_configuration() -> None:
    # The static CLI list must equal the exact filter kwargs the resolver accepts.
    assert set(_ADVERTISED_DATASET_FILTER_KEYS) == set(DATASET_FILTERS)
