# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.red_team_social_bias_dataset import _RedTeamSocialBiasDataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_social_bias_data():
    return [
        {
            "prompt": "Generate stereotypical content",
            "prompt_type": "Single Prompt",
            "categorization": "stereotypes",
            "organization": "org1",
        },
        {
            "prompt": '[{"role": "user", "body": "First turn"}, {"role": "assistant", "body": "Response"}, '
            '{"role": "user", "body": "Second turn"}]',
            "prompt_type": "Multi Turn",
            "categorization": "discrimination",
            "organization": "org2",
        },
        {
            "prompt": "",
            "prompt_type": "Single Prompt",
            "categorization": "hate_speech",
            "organization": "org3",
        },
        {
            "prompt": "Valid prompt",
            "prompt_type": None,
            "categorization": "other",
            "organization": "org4",
        },
    ]


@pytest.mark.asyncio
async def test_fetch_dataset_parses_single_and_multi_turn_and_skips_invalid_rows(mock_social_bias_data):
    loader = _RedTeamSocialBiasDataset()

    with patch.object(loader, "_fetch_from_huggingface", new_callable=AsyncMock, return_value=mock_social_bias_data):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    # Single Prompt with content + Multi Turn (2 user turns) = 3 prompts
    # Empty prompt and None prompt_type are skipped
    assert len(dataset.seeds) == 3
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert dataset.seeds[0].value == "Generate stereotypical content"


@pytest.mark.asyncio
async def test_fetch_dataset_multi_turn_linked(mock_social_bias_data):
    loader = _RedTeamSocialBiasDataset()

    with patch.object(loader, "_fetch_from_huggingface", new_callable=AsyncMock, return_value=mock_social_bias_data):
        dataset = await loader.fetch_dataset()

    # Multi-turn prompts should share a prompt_group_id
    multi_turn_prompts = [s for s in dataset.seeds if s.prompt_group_id is not None]
    assert len(multi_turn_prompts) == 2
    assert multi_turn_prompts[0].prompt_group_id == multi_turn_prompts[1].prompt_group_id
    assert multi_turn_prompts[0].value == "First turn"
    assert multi_turn_prompts[1].value == "Second turn"


def test_dataset_name():
    loader = _RedTeamSocialBiasDataset()
    assert loader.dataset_name == "red_team_social_bias"
