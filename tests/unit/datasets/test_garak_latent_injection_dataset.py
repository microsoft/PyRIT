# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the local Garak latent-injection seed datasets."""

import hashlib
import itertools
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pyrit.models import Seed, SeedDataset
from pyrit.scenario import DatasetSource
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


def test_empty_task_seeds_are_intentional_ingredients() -> None:
    seeds = _load("garak_latent_injection_tasks").seeds
    assert [seed.metadata["garak_class"] for seed in seeds if not seed.value] == [
        "LatentInjectionReport",
        "LatentInjectionResume",
        "LatentJailbreak",
    ]


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
def ingredient_seeds() -> dict[str, list[Seed[Any]]]:
    return {name: list(_load(name).seeds) for name in _FILES}


@pytest.mark.usefixtures("patch_central_database")
async def test_payload_split_preserves_complete_prompt_population_async(
    ingredient_seeds: dict[str, list[Seed[Any]]],
) -> None:
    config = LatentInjectionDatasetConfiguration(
        sources=[DatasetSource(name=name) for name in _FILES],
        families=LatentInjectionDatasetConfiguration.FAMILIES,
        max_total="all",
    )
    with patch.object(config, "_collect_named_seeds_async", return_value=ingredient_seeds):
        groups = await config.get_attack_seed_groups_async()
    assert len(groups) == 4266
    assert all(group.objective.value and group.prompts[0].value for group in groups)
    # Retain the pre-correction content fingerprint independently of logical IDs.
    content = sorted(
        (group.objective.metadata["family"], group.objective.value, group.prompts[0].value) for group in groups
    )
    assert hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest() == (
        "c0a779848569af008f63fe733ba039b40420f60ef0c32850042ab27a0894b6af"
    )
    # Objectives and prompts are unchanged; logical IDs now use ordered seed content hashes.
    expected = {
        "fact_eiffel": "0dcf9d19fd11c8b947af7371efedb6ac928d95c30d1a29f747956a51182fcd0d",
        "fact_legal": "5d4f85cef2be617315195c6b21b28f97faef347bb938632a1a9ece24d23fe1fb",
        "latent_jailbreak": "28c71dd3cdcf0eec8fe9c1d3c70777cc1d6a314f2f11df32417669052a405fb9",
        "report": "f26b77c5fd5a54ee470f1e2c466421cba86b2165c0157c1a7ef93b1ab5ca0342",
        "resume": "e327a7225ebd8b787f15a846985f6a405d8896cd665d876581cbf3040feba8c1",
        "translation_fr": "c5d31d7a1f347ab6853e28dffe6618a3ee6f8bf59ba5dd61e608ea29940babac",
        "translation_zh": "ceb67f22e2f5dc64b6850da13c0ebc195bfd6db16308fddeecde8e9acb00ad28",
        "whois": "30ca5c8633ddc4a7535e19dfec7614381b87c85ca08d13039d21d41d0ae58242",
        "whois_snippet": "7d3f54417e2555733c83eaa19011fe4066db5ae0bdfed71d0a615e03aead28e3",
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
