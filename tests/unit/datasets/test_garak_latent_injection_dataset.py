# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Structural tests for the garak latent-injection local datasets."""

from pathlib import Path

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.models import SeedDataset

LOCAL_DATASET_PATH = Path(DATASETS_PATH) / "seed_datasets" / "local" / "garak"

TASKS_FILE = LOCAL_DATASET_PATH / "latent_injection_tasks.prompt"
CONTEXTS_FILE = LOCAL_DATASET_PATH / "latent_injection_contexts.prompt"
INSTRUCTIONS_FILE = LOCAL_DATASET_PATH / "latent_injection_instructions.prompt"
PAYLOADS_FILE = LOCAL_DATASET_PATH / "latent_injection_payloads.prompt"
TRIGGERS_FILE = LOCAL_DATASET_PATH / "latent_injection_triggers.prompt"

INJECTION_MARKER = "<|INJECTION|>"
PAYLOAD_MARKER = "<|PAYLOAD|>"
TRIGGER_MARKER = "<|TRIGGER|>"

EXPECTED_FAMILIES = {
    "translation_fr",
    "translation_zh",
    "report",
    "resume",
    "fact_eiffel",
    "fact_legal",
    "whois",
    "whois_snippet",
    "latent_jailbreak",
}

# Families whose success is scored with an exact substring trigger (every family except
# latent_jailbreak, which has no exact-trigger contract).
EXACT_TRIGGER_FAMILIES = EXPECTED_FAMILIES - {"latent_jailbreak"}


@pytest.fixture(scope="module")
def tasks() -> SeedDataset:
    return SeedDataset.from_yaml_file(TASKS_FILE)


@pytest.fixture(scope="module")
def contexts() -> SeedDataset:
    return SeedDataset.from_yaml_file(CONTEXTS_FILE)


@pytest.fixture(scope="module")
def instructions() -> SeedDataset:
    return SeedDataset.from_yaml_file(INSTRUCTIONS_FILE)


@pytest.fixture(scope="module")
def payloads() -> SeedDataset:
    return SeedDataset.from_yaml_file(PAYLOADS_FILE)


@pytest.fixture(scope="module")
def triggers() -> SeedDataset:
    return SeedDataset.from_yaml_file(TRIGGERS_FILE)


def _families(dataset: SeedDataset, key: str = "family") -> set[str]:
    families = set()
    for seed in dataset.seeds:
        value = (seed.metadata or {}).get(key)
        assert value, f"Seed in {dataset.dataset_name} is missing '{key}' metadata: {seed.value[:60]!r}"
        families.update(str(value).split())
    return families


@pytest.mark.parametrize(
    ("path", "expected_name"),
    [
        (TASKS_FILE, "garak_latent_injection_tasks"),
        (CONTEXTS_FILE, "garak_latent_injection_contexts"),
        (INSTRUCTIONS_FILE, "garak_latent_injection_instructions"),
        (PAYLOADS_FILE, "garak_latent_injection_payloads"),
        (TRIGGERS_FILE, "garak_latent_injection_triggers"),
    ],
)
def test_dataset_name_and_source(path: Path, expected_name: str) -> None:
    dataset = SeedDataset.from_yaml_file(path)
    assert dataset.dataset_name == expected_name
    for seed in dataset.seeds:
        assert seed.dataset_name == expected_name
        assert seed.source
        assert seed.harm_categories


def test_all_families_covered(
    tasks: SeedDataset,
    contexts: SeedDataset,
    instructions: SeedDataset,
    payloads: SeedDataset,
    triggers: SeedDataset,
) -> None:
    """Every garak latent-injection carrier family has tasks, contexts, and triggers."""
    for dataset, key in ((tasks, "family"), (contexts, "family"), (triggers, "family")):
        assert _families(dataset, key) == EXPECTED_FAMILIES
    assert _families(instructions, "families") == EXPECTED_FAMILIES
    # The resume family's payloads are the universal bare-trigger template plus none of its own.
    assert _families(payloads, "families") == EXPECTED_FAMILIES - {"resume"}


def test_contexts_carry_injection_marker(contexts: SeedDataset) -> None:
    for seed in contexts.seeds:
        assert INJECTION_MARKER in seed.value, f"Carrier missing injection marker: {seed.value[:60]!r}"


def test_instructions_carry_payload_marker(instructions: SeedDataset) -> None:
    for seed in instructions.seeds:
        assert PAYLOAD_MARKER in seed.value, f"Instruction missing payload marker: {seed.value[:60]!r}"


def test_payloads_carry_trigger_marker(payloads: SeedDataset) -> None:
    for seed in payloads.seeds:
        assert TRIGGER_MARKER in seed.value, f"Payload missing trigger marker: {seed.value[:60]!r}"


def test_exact_trigger_families_have_unique_triggers(triggers: SeedDataset) -> None:
    """Each exact-trigger family's expected strings are unique (they name atomic attacks)."""
    by_family: dict[str, list[str]] = {}
    for seed in triggers.seeds:
        family = str((seed.metadata or {})["family"])
        if family in EXACT_TRIGGER_FAMILIES:
            by_family.setdefault(family, []).append(seed.value)
    assert set(by_family) == EXACT_TRIGGER_FAMILIES
    for family, values in by_family.items():
        assert len(values) == len(set(values)), f"Duplicate triggers in family '{family}'"


def test_no_garak_markers_leak() -> None:
    """Ported content must use the PyRIT markers, not garak's internal ones."""
    for path in (TASKS_FILE, CONTEXTS_FILE, INSTRUCTIONS_FILE, PAYLOADS_FILE, TRIGGERS_FILE):
        content = path.read_text(encoding="utf-8")
        assert "<|garak_injection|>" not in content
        assert "<|garak_payload|>" not in content
        assert "<|garak_trigger|>" not in content
        assert "{INJECTION_MARKER}" not in content
        assert "{PAYLOAD_MARKER}" not in content
        assert "{TRIGGER_MARKER}" not in content
