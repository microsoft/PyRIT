# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.pku_safe_rlhf_dataset import _PKUSafeRLHFDataset
from pyrit.models import SeedDataset, SeedPrompt

PKU_EXPECTED_HARM_CATEGORIES = [
    ("Animal Abuse", ["OTHER"]),
    ("Copyright Issues", ["COPYRIGHT"]),
    ("Cybercrime", ["MALWARE", "COORDINATION_HARM"]),
    ("Discriminatory Behavior", ["HATE_SPEECH", "REPRESENTATIONAL"]),
    ("Disrupting Public Order", ["COORDINATION_HARM"]),
    ("Drugs", ["DRUG_USE", "REGULATED_GOODS"]),
    ("Economic Crime", ["SCAMS", "DECEPTION"]),
    ("Endangering National Security", ["COORDINATION_HARM", "INFRASTRUCTURE_RISK", "MILITARY"]),
    ("Endangering Public Health", ["PUBLIC_HEALTH"]),
    ("Environmental Damage", ["OTHER"]),
    ("Human Trafficking", ["COORDINATION_HARM", "SEXUAL_SOLICITATION"]),
    ("Insulting Behavior", ["HARASSMENT"]),
    ("Mental Manipulation", ["DECEPTION"]),
    ("Physical Harm", ["VIOLENT_CONTENT"]),
    ("Privacy Violation", ["PPI"]),
    ("Psychological Harm", ["MENTAL_HEALTH", "EMOTIONAL"]),
    ("Sexual Content", ["SEXUAL_CONTENT"]),
    ("Violence", ["VIOLENT_CONTENT"]),
    ("White-Collar Crime", ["SCAMS", "DECEPTION"]),
]


@pytest.fixture
def mock_pku_data():
    return [
        {
            "prompt": "How to hack a system?",
            "is_response_0_safe": False,
            "is_response_1_safe": True,
            "response_0_harm_category": {"Cybercrime": True, "Violence": False},
            "response_1_harm_category": {"Cybercrime": False, "Violence": False},
        },
        {
            "prompt": "What is the weather today?",
            "is_response_0_safe": True,
            "is_response_1_safe": True,
            "response_0_harm_category": {"Cybercrime": False, "Violence": False},
            "response_1_harm_category": {"Cybercrime": False, "Violence": False},
        },
        {
            "prompt": "How to create a weapon?",
            "is_response_0_safe": False,
            "is_response_1_safe": False,
            "response_0_harm_category": {"Violence": True, "Cybercrime": False},
            "response_1_harm_category": {"Violence": True, "Cybercrime": False},
        },
    ]


async def test_fetch_dataset_includes_all_prompts(mock_pku_data):
    loader = _PKUSafeRLHFDataset(include_safe_prompts=True)

    with patch.object(loader, "_fetch_from_huggingface_async", new_callable=AsyncMock, return_value=mock_pku_data):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 3
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert dataset.seeds[0].harm_categories == ["MALWARE", "COORDINATION_HARM"]
    assert dataset.seeds[1].harm_categories == []
    assert dataset.seeds[2].harm_categories == ["VIOLENT_CONTENT"]
    # Original PKU category labels are preserved in metadata (provenance).
    assert dataset.seeds[0].metadata == {"pku_categories": "Cybercrime"}
    assert dataset.seeds[1].metadata is None
    assert dataset.seeds[2].metadata == {"pku_categories": "Violence"}


async def test_fetch_dataset_excludes_safe_prompts(mock_pku_data):
    loader = _PKUSafeRLHFDataset(include_safe_prompts=False)

    with patch.object(loader, "_fetch_from_huggingface_async", new_callable=AsyncMock, return_value=mock_pku_data):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    values = [s.value for s in dataset.seeds]
    assert "What is the weather today?" not in values


async def test_fetch_dataset_filters_by_harm_category(mock_pku_data):
    loader = _PKUSafeRLHFDataset(include_safe_prompts=True, filter_harm_categories=["Cybercrime"])

    with patch.object(loader, "_fetch_from_huggingface_async", new_callable=AsyncMock, return_value=mock_pku_data):
        dataset = await loader.fetch_dataset_async()

    # Only the first item has Cybercrime=True; safe item has no harm categories so it's excluded
    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].value == "How to hack a system?"


def test_dataset_name():
    loader = _PKUSafeRLHFDataset()
    assert loader.dataset_name == "pku_safe_rlhf"


@pytest.mark.parametrize(("native_label", "expected_categories"), PKU_EXPECTED_HARM_CATEGORIES)
async def test_fetch_dataset_standardizes_all_native_harm_categories(native_label, expected_categories):
    loader = _PKUSafeRLHFDataset()
    data = [
        {
            "prompt": f"Prompt for {native_label}",
            "is_response_0_safe": False,
            "is_response_1_safe": True,
            "response_0_harm_category": {native_label: True},
            "response_1_harm_category": {native_label: False},
        }
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new_callable=AsyncMock, return_value=data):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].harm_categories == expected_categories
