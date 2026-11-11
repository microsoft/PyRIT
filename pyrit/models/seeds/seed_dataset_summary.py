# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SeedDatasetSummary:
    """Aggregate statistics and metadata for the stored seeds in one dataset."""

    dataset_name: str | None
    logical_examples: int
    seed_pieces: int
    objectives: int
    modalities: tuple[str, ...]
    harm_categories: tuple[str, ...]
    has_unlabeled_harm_categories: bool
    origins: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SeedDatasetGroupProfile:
    """Counts of stored groups sharing structural facts, without seed bodies or schema validation."""

    dataset_name: str
    group_count: int
    single_objective: bool
    single_seed: bool
    has_user_prompt: bool
    has_non_user_prompt: bool
    has_simulated_conversation: bool
    has_adversarial_prompts: bool
    metadata_keys: tuple[str, ...] = ()
