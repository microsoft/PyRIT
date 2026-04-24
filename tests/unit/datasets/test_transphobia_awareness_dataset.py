# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pandas as pd
import pytest

from pyrit.datasets.seed_datasets.remote.transphobia_awareness_dataset import (
    _TransphobiaAwarenessDataset,
)
from pyrit.models import SeedDataset


class TestTransphobiaAwarenessDataset:
    """Unit tests for _TransphobiaAwarenessDataset."""

    def test_dataset_name(self):
        """Test that dataset_name property returns correct value."""
        dataset = _TransphobiaAwarenessDataset()
        assert dataset.dataset_name == "transphobia_awareness"

    def test_init_with_default_source(self):
        """Test initialization with default source URL."""
        dataset = _TransphobiaAwarenessDataset()
        assert dataset.source == _TransphobiaAwarenessDataset.RATINGS_URL

    def test_init_with_custom_source(self):
        """Test initialization with custom source URL."""
        custom_url = "https://example.com/custom.xlsx"
        dataset = _TransphobiaAwarenessDataset(source=custom_url)
        assert dataset.source == custom_url

    @pytest.mark.asyncio
    async def test_fetch_dataset_with_mock_data(self):
        """Test fetching dataset with mocked pandas read_excel."""
        # Create mock DataFrame
        mock_df = pd.DataFrame(
            {
                "Quora Question": ["Question 1", "Question 2"],
                "keyword": ["transgender", "Trans"],
                "Question sentiment: How transphobic is the question?\n\n"
                "Rate on a scale of 1-6, where 6 is the most transphobic": [
                    "3 (Neutral)",
                    "5 (Transphobic)",
                ],
            }
        )

        dataset_loader = _TransphobiaAwarenessDataset()

        with patch("pandas.read_excel", return_value=mock_df):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            assert isinstance(dataset, SeedDataset)
            assert dataset.dataset_name == "transphobia_awareness"
            assert len(dataset.seeds) == 2
            assert dataset.seeds[0].value == "Question 1"
            assert dataset.seeds[1].value == "Question 2"
            assert dataset.seeds[0].harm_categories == ["transphobia"]
            assert dataset.seeds[0].metadata["keyword"] == "transgender"
            assert dataset.seeds[1].metadata["keyword"] == "transgender"  # Mapped from "Trans"
            assert dataset.seeds[0].metadata["question_sentiment"] == 3.0
            assert dataset.seeds[1].metadata["question_sentiment"] == 5.0

    @pytest.mark.asyncio
    async def test_fetch_dataset_keyword_mapping(self):
        """Test that keywords are properly mapped."""
        mock_df = pd.DataFrame(
            {
                "Quora Question": ["Q1", "Q2", "Q3"],
                "keyword": ["Trans", "Transgender", "nonbinary"],
                "Question sentiment: How transphobic is the question?\n\n"
                "Rate on a scale of 1-6, where 6 is the most transphobic": [
                    "1",
                    "2",
                    "3",
                ],
            }
        )

        dataset_loader = _TransphobiaAwarenessDataset()

        with patch("pandas.read_excel", return_value=mock_df):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            # All Trans and Transgender should be mapped to "transgender"
            assert dataset.seeds[0].metadata["keyword"] == "transgender"
            assert dataset.seeds[1].metadata["keyword"] == "transgender"
            # nonbinary should be mapped to "non-binary"
            assert dataset.seeds[2].metadata["keyword"] == "non-binary"

    @pytest.mark.asyncio
    async def test_fetch_dataset_handles_missing_sentiment(self):
        """Test that missing sentiment values are handled gracefully."""
        mock_df = pd.DataFrame(
            {
                "Quora Question": ["Question with sentiment", "Question without sentiment"],
                "keyword": ["transgender", "transgender"],
                "Question sentiment: How transphobic is the question?\n\n"
                "Rate on a scale of 1-6, where 6 is the most transphobic": [
                    "4 (Slightly Transphobic)",
                    None,
                ],
            }
        )

        dataset_loader = _TransphobiaAwarenessDataset()

        with patch("pandas.read_excel", return_value=mock_df):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            # First seed should have sentiment
            assert "question_sentiment" in dataset.seeds[0].metadata
            assert dataset.seeds[0].metadata["question_sentiment"] == 4.0

            # Second seed should not have sentiment key since it's None
            assert "question_sentiment" not in dataset.seeds[1].metadata
