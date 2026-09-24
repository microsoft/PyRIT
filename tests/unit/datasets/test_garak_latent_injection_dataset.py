# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the local Garak latent-injection seed datasets."""

import hashlib
import itertools
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.models import Seed, SeedDataset
from pyrit.scenario.scenarios.garak.latent_injection import LatentInjectionDatasetConfiguration

_DATASET_DIR = Path(__file__).parent.parent.parent.parent / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"

_FILES = {
    "garak_latent_injection_contexts": "latent_injection_contexts.prompt",
    "garak_latent_injection_tasks": "latent_injection_tasks.prompt",
    "garak_latent_injection_instructions": "latent_injection_instructions.prompt",
    "garak_latent_injection_payload_templates": "latent_injection_payload_templates.prompt",
    "garak_latent_injection_triggers": "latent_injection_triggers.prompt",
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
        assert family in LatentInjectionDatasetConfiguration.FAMILIES, f"unknown family {family!r} in {dataset_name}"


def test_every_carrier_family_has_seeds_for_each_role():
    """Each family the scenario can run must have a task, an instruction, and a payload."""
    for role in ("tasks", "instructions", "payload_templates", "triggers"):
        dataset_name = f"garak_latent_injection_{role}"
        families = {(seed.metadata or {}).get("family") for seed in _load(dataset_name).seeds}
        assert set(LatentInjectionDatasetConfiguration.FAMILIES) == families, f"{role} is missing families"


def test_context_families_resolve_to_stored_contexts():
    """``whois_snippet`` sources its documents from the whois family, so it stores none of its own."""
    stored = {(seed.metadata or {}).get("family") for seed in _load("garak_latent_injection_contexts").seeds}
    for family in LatentInjectionDatasetConfiguration.FAMILIES:
        assert ("whois" if family == "whois_snippet" else family) in stored


def test_carrier_documents_contain_exactly_one_injection_marker():
    for seed in _load("garak_latent_injection_contexts").seeds:
        family = seed.metadata["family"]
        if family not in {"fact_eiffel", "fact_legal"}:
            assert seed.value.count(LatentInjectionDatasetConfiguration.INJECTION_MARKER) == 1, seed.value[:80]
        else:
            # Snippet families store raw paragraphs; the scenario inserts or preserves the marker.
            assert seed.value.count(LatentInjectionDatasetConfiguration.INJECTION_MARKER) <= 1


def test_injection_instructions_contain_a_payload_marker():
    for seed in _load("garak_latent_injection_instructions").seeds:
        assert LatentInjectionDatasetConfiguration.PAYLOAD_MARKER in seed.value


def test_separate_templates_and_triggers_preserve_expanded_payloads() -> None:
    config = LatentInjectionDatasetConfiguration
    templates = _load(config.PAYLOAD_DATASET_NAME).seeds
    triggers = _load(config.TRIGGER_DATASET_NAME).seeds
    assert len(templates) == 20
    assert len(triggers) == 39
    assert all(config.TRIGGER_MARKER in seed.value for seed in templates)
    assert all(seed.value.strip() and config.TRIGGER_MARKER not in seed.value for seed in triggers)
    expanded = sorted(
        (template.metadata["family"], template.value.replace(config.TRIGGER_MARKER, trigger.value), trigger.value)
        for template, trigger in itertools.product(templates, triggers)
        if template.metadata["family"] == trigger.metadata["family"]
    )
    assert len(expanded) == 75
    assert hashlib.sha256(json.dumps(expanded, ensure_ascii=False).encode()).hexdigest() == (
        "df600f28f392fab44293c3d0e7318833d03ba3ba491412226110db3490e66f97"
    )


@pytest.fixture
def ingredient_seeds() -> dict[str, list[Seed]]:
    return {name: list(_load(name).seeds) for name in _FILES}


@pytest.mark.usefixtures("patch_central_database")
async def test_payload_split_preserves_complete_prompt_population_async(
    ingredient_seeds: dict[str, list[Seed]],
) -> None:
    config = LatentInjectionDatasetConfiguration(
        dataset_names=list(_FILES), families=LatentInjectionDatasetConfiguration.FAMILIES
    )
    with patch.object(config, "_collect_named_seeds_async", return_value=ingredient_seeds):
        groups = await config.get_attack_seed_groups_async()
    assert len(groups) == 4266
    # These fingerprints include the objectives, prompts, and logical IDs before the payload split.
    expected = {
        "fact_eiffel": "ef849eeea99d49777c19b74eaf321f072b9c310bcc9c37b3d1cb431f59319786",
        "fact_legal": "ad37fab4a5eea7587de336c6be433b9e6318d3d8f05e22a78998cb5151ef112e",
        "latent_jailbreak": "aa66b8a4f00493a991c4eac078900373e73847141c87802cf83695979572ce2f",
        "report": "40cabdb6ee290a1e141e9050b39696958e8de3a6f741ef3a763193396b6896a9",
        "resume": "8adfb55c327a4a9d7d2809a72c03fd88c516e5afe1944902afc53516ec451af5",
        "translation_fr": "7b3be9df7af9fbd97a8fd4d3e7836184f59de0a78007211b9d24c30bec4cc112",
        "translation_zh": "9cf4e66d299e6eebc27cf727109e0c39314ac2ea89eb3d99c2fac6c275a2df41",
        "whois": "fdfd45739f7c3c586d679277f4bb948194c952d01a48be1c6f918404ebf7208c",
        "whois_snippet": "330ef45ddf294e8129375115f2ea0184a52b92ce069870b6f00052c9638af308",
    }
    for family, fingerprint in expected.items():
        rows = sorted(
            (group.objective.value, group.prompts[0].value, group.logical_id)
            for group in groups
            if group.objective.metadata["family"] == family
        )
        assert hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest() == fingerprint, family


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
