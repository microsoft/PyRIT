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
