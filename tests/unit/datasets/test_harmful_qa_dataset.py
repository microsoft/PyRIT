# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.harmful_qa_dataset import _HarmfulQADataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_harmful_qa_data():
    """Mock data for HarmfulQA dataset.

    The first three rows use real upstream ``id`` values that exist in the in-code
    row-level harm-category map (1 -> HATE_SPEECH, 321 -> INSECURE_CODE, 721 -> CBRN).
    The last row uses an id outside the audited range to exercise the coarse-topic
    fallback path. Harm-category assignment is keyed on ``id``, so the ``question``
    text here is deliberately a benign synthetic placeholder rather than real dataset
    content -- the loader must not depend on (or redistribute) the upstream prompts.
    """
    return [
        {
            "topic": "Social Sciences",
            "subtopic": "Cultural Studies",
            "blue_conversations": "{}",
            "red_conversations": "{}",
            "id": 1,
            "question": "Synthetic placeholder prompt for unit testing (id 1).",
        },
        {
            "topic": "Science and Technology",
            "subtopic": "Computer Science",
            "blue_conversations": "{}",
            "red_conversations": "{}",
            "id": 321,
            "question": "Synthetic placeholder prompt for unit testing (id 321).",
        },
        {
            "topic": "Science and Technology",
            "subtopic": "Biology",
            "blue_conversations": "{}",
            "red_conversations": "{}",
            "id": 721,
            "question": "Synthetic placeholder prompt for unit testing (id 721).",
        },
        {
            "topic": "Totally Made Up Topic",
            "subtopic": "Unknown",
            "blue_conversations": "{}",
            "red_conversations": "{}",
            "id": 999999,
            "question": "Synthetic placeholder prompt for unit testing (unmapped id).",
        },
    ]


class TestHarmfulQADataset:
    """Test the HarmfulQA dataset loader."""

    async def test_fetch_dataset(self, mock_harmful_qa_data):
        """Test fetching HarmfulQA dataset."""
        loader = _HarmfulQADataset()

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_harmful_qa_data)):
            dataset = await loader.fetch_dataset_async()

            assert isinstance(dataset, SeedDataset)
            assert len(dataset.seeds) == 4
            assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)

            # Row-level map assigns per-question canonical harm categories rather than
            # a coarse per-topic label.
            first_prompt = dataset.seeds[0]
            assert first_prompt.value == "Synthetic placeholder prompt for unit testing (id 1)."
            assert first_prompt.harm_categories == ["HATE_SPEECH"]
            assert first_prompt.metadata["topic"] == "Social Sciences"
            assert first_prompt.metadata["subtopic"] == "Cultural Studies"

            assert dataset.seeds[1].harm_categories == ["INSECURE_CODE"]
            assert dataset.seeds[2].harm_categories == ["CBRN"]

            # Unmapped row falls back to the coarse topic mapping (unknown topic -> OTHER).
            assert dataset.seeds[3].harm_categories == ["OTHER"]

    def test_dataset_name(self):
        """Test dataset_name property."""
        loader = _HarmfulQADataset()
        assert loader.dataset_name == "harmful_qa"
