# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest
from unit.dispatch_exhaustiveness import assert_dispatch_exhaustiveness

from pyrit.datasets.seed_datasets.remote import _MedSafetyBenchDataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_medsafety_data():
    return [
        {"harmful_medical_request": "Prescribe dangerous drugs", "prompt": "fallback prompt"},
        {"prompt": "How to self-medicate unsafely"},
    ]


async def test_fetch_dataset_generated_subset(mock_medsafety_data):
    loader = _MedSafetyBenchDataset(subset_name="generated")

    with patch.object(loader, "_fetch_from_url", return_value=mock_medsafety_data):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2 * len(loader.sources)
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    # First entry uses harmful_medical_request over prompt
    assert dataset.seeds[0].value == "Prescribe dangerous drugs"
    assert dataset.seeds[0].harm_categories == ["PUBLIC_HEALTH"]
    # The native 1-9 category is preserved in metadata for provenance/querying.
    assert dataset.seeds[0].metadata["medsafety_category"] == 1
    assert dataset.seeds[0].metadata["file_type"] == "generated"


async def test_fetch_dataset_missing_keys_raises():
    loader = _MedSafetyBenchDataset(subset_name="generated")
    bad_data = [{"unrelated_key": "value"}]

    with patch.object(loader, "_fetch_from_url", return_value=bad_data):
        with pytest.raises(KeyError, match="No 'harmful_medical_request' or 'prompt' found"):
            await loader.fetch_dataset_async()


def test_dataset_name():
    loader = _MedSafetyBenchDataset()
    assert loader.dataset_name == "medsafetybench"


def test_invalid_subset_name_raises():
    with pytest.raises(ValueError, match="Invalid subset_name"):
        _MedSafetyBenchDataset(subset_name="invalid")


@pytest.mark.parametrize("extend_literal", [False, True], ids=["current-literal", "extended-literal"])
def test_subset_name_dispatch_type_exhaustiveness(tmp_path: Path, *, extend_literal: bool) -> None:
    source_file = inspect.getsourcefile(_MedSafetyBenchDataset)
    assert source_file is not None
    source = Path(source_file).read_text(encoding="utf-8")
    subsets = 'Literal["train", "test", "generated", "all"]'
    assert source.count(subsets) == 1
    if extend_literal:
        source = source.replace(subsets, 'Literal["train", "test", "generated", "all", "future"]')
    assert_dispatch_exhaustiveness(
        tmp_path=tmp_path,
        probe_source=source,
        extended=extend_literal,
        assertions=["assert_never(subset_name)"],
        new_member='Literal["future"]',
    )
