# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the local Garak ProPILE datasets."""

from pyrit.datasets import SeedDatasetProvider


async def test_propile_record_dataset_preserves_provenance():
    datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["garak_propile_pii"])
    dataset = datasets[0]

    assert len(dataset.seeds) == 26
    assert all(seed.metadata and seed.metadata.get("source_dataset") for seed in dataset.seeds)
    assert all(seed.metadata and seed.metadata.get("source_id") for seed in dataset.seeds)


async def test_propile_template_dataset_preserves_categories():
    datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["garak_propile_templates"])
    dataset = datasets[0]

    categories = {(seed.metadata or {}).get("category") for seed in dataset.seeds}
    assert len(dataset.seeds) == 24
    assert {"twin_email", "triplet_phone", "quadruplet_address", "unstructured_relation"} <= categories
