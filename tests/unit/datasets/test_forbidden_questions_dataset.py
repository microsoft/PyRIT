# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.forbidden_questions_dataset import (
    _ForbiddenQuestionsDataset,
)
from pyrit.models import SeedDataset, SeedPrompt

# The dataset's actual ``content_policy_name`` values (abbreviated relative to the
# paper's scenario names) mapped to their expected standardized harm categories.
FORBIDDEN_EXPECTED_HARM_CATEGORIES = [
    ("Illegal Activity", ["COORDINATION_HARM"]),
    ("Hate Speech", ["HATE_SPEECH"]),
    ("Malware", ["MALWARE"]),
    ("Physical Harm", ["VIOLENT_CONTENT", "COORDINATION_HARM"]),
    ("Economic Harm", ["SCAMS", "DECEPTION"]),
    ("Fraud", ["SCAMS", "DECEPTION"]),
    ("Pornography", ["SEXUAL_CONTENT"]),
    ("Political Lobbying", ["CAMPAIGNING"]),
    ("Privacy Violence", ["PPI"]),
    ("Legal Opinion", ["LEGAL_ADVICE"]),
    ("Financial Advice", ["FINANCIAL_ADVICE"]),
    ("Health Consultation", ["HEALTH_DIAGNOSIS"]),
    ("Gov Decision", ["HIGH_RISK_GOVERNMENT", "COORDINATION_HARM"]),
]


class TestForbiddenQuestionsDataset:
    """Test the Forbidden Questions dataset loader."""

    def test_dataset_name(self):
        loader = _ForbiddenQuestionsDataset()
        assert loader.dataset_name == "forbidden_questions"

    @pytest.mark.parametrize(("native_label", "expected_categories"), FORBIDDEN_EXPECTED_HARM_CATEGORIES)
    async def test_fetch_dataset_standardizes_all_native_harm_categories(self, native_label, expected_categories):
        loader = _ForbiddenQuestionsDataset()
        data = [{"question": f"Question for {native_label}", "content_policy_name": native_label}]

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=data)):
            dataset = await loader.fetch_dataset_async()

        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 1
        assert isinstance(dataset.seeds[0], SeedPrompt)
        assert dataset.seeds[0].harm_categories == expected_categories
        assert dataset.seeds[0].metadata["forbidden_questions_content_policy_name"] == native_label
