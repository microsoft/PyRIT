# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections import Counter

from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedObjective

DATASET_NAME = "adversarial_benchmark_v1"
EXPECTED_CATEGORY_COUNTS = {
    "election_critical_information": 15,
    "hate_v3": 15,
    "inference_sensitive_attributes": 15,
    "offensive_cyber_v2": 15,
    "self_harm_v3": 15,
    "sensitive_data_leakage": 15,
    "sexual_v3": 15,
    "violence_v3": 15,
}
EXPECTED_SPLIT_COUNTS = {"train": 64, "test": 56}


async def test_adversarial_benchmark_v1_resolves_by_name_async() -> None:
    datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=[DATASET_NAME])

    assert len(datasets) == 1
    dataset = datasets[0]
    assert dataset.dataset_name == DATASET_NAME
    assert len(dataset.seeds) == 120
    assert all(isinstance(seed, SeedObjective) for seed in dataset.seeds)
    assert all(seed.dataset_name == DATASET_NAME for seed in dataset.seeds)
    assert len(dataset.seed_groups) == 120
    assert all(not group.prompts for group in dataset.seed_groups)

    category_counts = Counter(category for seed in dataset.objectives for category in seed.harm_categories)
    assert category_counts == EXPECTED_CATEGORY_COUNTS

    split_counts = Counter(seed.metadata["source_split"] for seed in dataset.objectives)
    assert split_counts == EXPECTED_SPLIT_COUNTS
    assert len({seed.metadata["behavior_id"] for seed in dataset.objectives}) == 52

    for category in EXPECTED_CATEGORY_COUNTS:
        category_seeds = [seed for seed in dataset.objectives if category in seed.harm_categories]
        assert Counter(seed.metadata["source_split"] for seed in category_seeds) == {"train": 8, "test": 7}

    assert all("Target behavior:" in seed.value for seed in dataset.objectives)
    assert all("Behavior definition:" in seed.value for seed in dataset.objectives)
    assert all("Request:" in seed.value for seed in dataset.objectives)
