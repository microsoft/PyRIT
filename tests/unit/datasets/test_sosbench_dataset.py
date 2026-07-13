# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.sosbench_dataset import _SOSBenchDataset
from pyrit.models import SeedDataset, SeedPrompt

# The dataset's actual ``subject`` values mapped to their expected standardized
# harm categories. The data uses "medical"/"pharmacy" (not "medicine"/"pharmacology"),
# which the default alias map does not cover, so every subject must be handled by the
# loader's own overrides to avoid falling through to OTHER.
SOSBENCH_EXPECTED_HARM_CATEGORIES = [
    ("chemistry", ["CBRN"]),
    ("biology", ["PUBLIC_HEALTH"]),
    ("physics", ["CBRN"]),
    ("medical", ["PUBLIC_HEALTH"]),
    ("pharmacy", ["PSEUDO_PHARMA"]),
    ("psychology", ["MENTAL_HEALTH"]),
]


class TestSOSBenchDataset:
    """Test the SOSBench dataset loader."""

    def test_dataset_name(self):
        loader = _SOSBenchDataset()
        assert loader.dataset_name == "sosbench"

    @pytest.mark.parametrize(("subject", "expected_categories"), SOSBENCH_EXPECTED_HARM_CATEGORIES)
    async def test_fetch_dataset_standardizes_all_subjects(self, subject, expected_categories):
        loader = _SOSBenchDataset()
        data = [{"goal": f"Goal for {subject}", "original_term": "term", "subject": subject}]

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=data)):
            dataset = await loader.fetch_dataset_async()

        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 1
        assert isinstance(dataset.seeds[0], SeedPrompt)
        assert dataset.seeds[0].harm_categories == expected_categories
        assert dataset.seeds[0].metadata["sosbench_subject"] == subject

    async def test_fetch_dataset_no_subject_returns_empty_categories(self):
        # Rows whose subject is missing should not raise; they resolve to no categories.
        loader = _SOSBenchDataset()
        data = [{"goal": "Goal without subject", "original_term": "term", "subject": None}]

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=data)):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].harm_categories == []
        assert dataset.seeds[0].metadata["sosbench_subject"] is None
