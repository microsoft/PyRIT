# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the local Garak latent-injection seed datasets."""

from pathlib import Path

import pytest

from pyrit.models import SeedDataset
from pyrit.scenario.scenarios.garak.latent_injection import LatentInjection

_DATASET_DIR = Path(__file__).parent.parent.parent.parent / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"

_FILES = {
    "garak_latent_injection_contexts": "latent_injection_contexts.prompt",
    "garak_latent_injection_tasks": "latent_injection_tasks.prompt",
    "garak_latent_injection_instructions": "latent_injection_instructions.prompt",
    "garak_latent_injection_payloads": "latent_injection_payloads.prompt",
}


def _load(dataset_name: str) -> SeedDataset:
    return SeedDataset.from_yaml_file(_DATASET_DIR / _FILES[dataset_name])


@pytest.mark.parametrize("dataset_name", sorted(_FILES))
def test_dataset_loads_with_expected_name(dataset_name):
    dataset = _load(dataset_name)
    assert dataset.dataset_name == dataset_name
    assert dataset.seeds


@pytest.mark.parametrize("dataset_name", sorted(_FILES))
def test_dataset_declares_garak_provenance(dataset_name):
    dataset = _load(dataset_name)
    seed = dataset.seeds[0]
    assert "NVIDIA/garak" in (seed.source or "")
    assert "blob/" in (seed.source or ""), "source must pin a garak commit, not a branch"
    assert "Garak" in ",".join(seed.groups or [])


@pytest.mark.parametrize("dataset_name", sorted(_FILES))
def test_every_seed_carries_a_known_family(dataset_name):
    for seed in _load(dataset_name).seeds:
        family = (seed.metadata or {}).get("family")
        assert family in LatentInjection.FAMILIES, f"unknown family {family!r} in {dataset_name}"


def test_every_carrier_family_has_seeds_for_each_role():
    """Each family the scenario can run must have a task, an instruction, and a payload."""
    for role in ("tasks", "instructions", "payloads"):
        dataset_name = f"garak_latent_injection_{role}"
        families = {(seed.metadata or {}).get("family") for seed in _load(dataset_name).seeds}
        assert set(LatentInjection.FAMILIES) == families, f"{role} is missing families"


def test_context_families_resolve_to_stored_contexts():
    """``whois_snippet`` sources its documents from the whois family, so it stores none of its own."""
    stored = {(seed.metadata or {}).get("family") for seed in _load("garak_latent_injection_contexts").seeds}
    for family in LatentInjection.FAMILIES.values():
        assert family.context_family in stored


def test_carrier_documents_contain_exactly_one_injection_marker():
    for seed in _load("garak_latent_injection_contexts").seeds:
        family = LatentInjection.FAMILIES[seed.metadata["family"]]
        if family.assembly is None:
            assert seed.value.count(LatentInjection.INJECTION_MARKER) == 1, seed.value[:80]
        else:
            # Snippet families store raw paragraphs; the scenario inserts or preserves the marker.
            assert seed.value.count(LatentInjection.INJECTION_MARKER) <= 1


def test_injection_instructions_contain_a_payload_marker():
    for seed in _load("garak_latent_injection_instructions").seeds:
        assert LatentInjection.PAYLOAD_MARKER in seed.value


def test_every_payload_declares_and_contains_its_trigger():
    for seed in _load("garak_latent_injection_payloads").seeds:
        trigger = (seed.metadata or {}).get("trigger")
        assert trigger, f"payload without a trigger: {seed.value[:60]}"
        assert trigger in seed.value


@pytest.mark.parametrize("dataset_name", sorted(_FILES))
def test_seed_values_are_unique_within_a_family(dataset_name):
    """The scenario orders each family's seeds by value; duplicates would leave that ambiguous."""
    counts: dict[tuple[str, str], int] = {}
    for seed in _load(dataset_name).seeds:
        key = (seed.metadata["family"], seed.value)
        counts[key] = counts.get(key, 0) + 1
    duplicates = [family for family, count in counts.items() if count > 1]
    assert not duplicates, f"duplicate values in {dataset_name}: {duplicates}"


def test_no_garak_markers_survive_the_port():
    for dataset_name in _FILES:
        for seed in _load(dataset_name).seeds:
            assert "<|garak_" not in seed.value
