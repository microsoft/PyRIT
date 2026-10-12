# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the local Garak snowballed-hallucination datasets."""

import re
from pathlib import Path

import pytest

from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader

_DATASET_DIRECTORY = Path(__file__).parents[3] / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"


@pytest.mark.parametrize(
    ("file_name", "dataset_name", "expected_pattern"),
    [
        (
            "snowball_graph_connectivity.prompt",
            "garak_snowball_graph_connectivity",
            r"Current flight information .*\n\nQuestion: Is there a series of flights .+\?",
        ),
        ("snowball_primes.prompt", "garak_snowball_primes", r"Is \d+ a prime number\?"),
        (
            "snowball_senators.prompt",
            "garak_snowball_senators",
            r"Was there ever a US senator that represented the state of .+ and whose alma mater was .+\?",
        ),
    ],
)
async def test_snowball_dataset_loads_100_unique_questions(
    file_name: str, dataset_name: str, expected_pattern: str
) -> None:
    provider = _LocalDatasetLoader(file_path=_DATASET_DIRECTORY / file_name)

    dataset = await provider.fetch_dataset_async()

    assert provider.dataset_name == dataset_name
    assert len(dataset.seeds) == 100
    assert len({seed.value for seed in dataset.seeds}) == 100
    assert all(re.fullmatch(expected_pattern, seed.value, flags=re.DOTALL) for seed in dataset.seeds)
    assert all(seed.harm_categories == ["hallucination"] for seed in dataset.seeds)
