# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.models import SeedDataset


@pytest.fixture
def mock_garak_dataset_fetch(garak_dataset_names: list[str]) -> Generator[None, None, None]:
    """Load fresh real corpora without rediscovering unrelated dataset providers."""
    directory = DATASETS_PATH / "seed_datasets" / "local" / "garak"
    datasets = {
        name: SeedDataset.from_yaml_file(directory / f"{name.removeprefix('garak_')}.prompt")
        for name in garak_dataset_names
    }

    async def fetch_datasets_async(*, dataset_names: list[str]) -> list[SeedDataset]:
        return [datasets[name] for name in dataset_names]

    with (
        patch.object(
            SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=list(datasets)
        ),
        patch.object(
            SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock, side_effect=fetch_datasets_async
        ),
    ):
        yield
