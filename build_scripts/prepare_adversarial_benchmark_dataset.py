# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Prepare the deterministic HarmBench subset used by the adversarial benchmark pipeline."""

import asyncio

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import SeedDataset
from pyrit.setup import SQLITE, initialize_pyrit_async

DATASET_NAME = "harmbench-balanced-14-v1"

_BEHAVIOR_IDS = (
    "morphine_homebake_detailed_instructions",
    "anfo_bomb_home_heat_level",
    "passage_da_vinci_code",
    "lyrics_viva_la_vida",
    "rootkit_code_obfuscation_mac_osx_1",
    "logs_deletion_sql_injection_template",
    "student_harassment_bullying_encouragement",
    "jewish_classmate_insults",
    "sexual_assault_romanticization_post",
    "pornographic_snuff_film",
    "airport_bomb_smuggling_plan",
    "emergency_communications_disruption",
    "fauci_recommendation_persuasive_reply",
    "election_fraud_2020_flip_result_reply_1",
)


def _build_balanced_dataset(*, source_dataset: SeedDataset) -> SeedDataset:
    """Select the benchmark objectives from a loaded HarmBench dataset."""
    seeds_by_id = {
        str(seed.metadata["BehaviorID"]): seed
        for seed in source_dataset.objectives
        if seed.metadata and "BehaviorID" in seed.metadata
    }
    missing = [behavior_id for behavior_id in _BEHAVIOR_IDS if behavior_id not in seeds_by_id]
    if missing:
        raise ValueError(f"HarmBench is missing required benchmark behavior IDs: {missing}")

    selected = []
    for behavior_id in _BEHAVIOR_IDS:
        seed = seeds_by_id[behavior_id].model_copy(deep=True)
        seed.dataset_name = DATASET_NAME
        seed.name = f"HarmBench: {behavior_id}"
        selected.append(seed)

    return SeedDataset(
        dataset_name=DATASET_NAME,
        name="HarmBench Balanced 14",
        description="A deterministic 14-objective HarmBench subset with two objectives per semantic category.",
        seeds=selected,
    )


async def _main_async() -> None:
    """Load, select, and persist the benchmark dataset."""
    await initialize_pyrit_async(
        memory_db_type=SQLITE,
        load_defaults=False,
        env_files=[],
        silent=True,
    )
    source_datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["harmbench"])
    source_dataset = source_datasets[0]
    benchmark_dataset = _build_balanced_dataset(source_dataset=source_dataset)
    memory = CentralMemory.get_memory_instance()
    await memory.add_seed_datasets_to_memory_async(
        datasets=[benchmark_dataset],
        added_by="prepare_adversarial_benchmark_dataset",
    )
    print(f"Loaded {len(benchmark_dataset.seeds)} objectives into dataset '{DATASET_NAME}'.")


def main() -> None:
    """Run the dataset preparation script."""
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
