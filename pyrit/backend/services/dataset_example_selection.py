# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve exact dataset example selections from memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.backend.services.dataset_example_fingerprint import (
    dataset_example_fingerprint,
    parse_dataset_selection_key,
)
from pyrit.memory import CentralMemory
from pyrit.models import (
    DatasetExampleReference,
    DatasetExampleSelection,
    DatasetExampleSelectionError,
    ScenarioDatasetRequest,
    SeedRecord,
)
from pyrit.scenario import ExactExampleDatasetConfiguration

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from pyrit.memory import MemoryInterface
    from pyrit.scenario import Scenario


@dataclass(frozen=True)
class ResolvedDatasetExample:
    """One selected example and the stored records it resolved to."""

    reference: DatasetExampleReference
    members: tuple[SeedRecord, ...]


def selection_conflicts_with_datasets(datasets: ScenarioDatasetRequest | None) -> bool:
    """
    Return whether named-dataset fields would change an exact population.

    Returns:
        bool: True when sources, filters, limits, or fetch are set.
    """
    if datasets is None:
        return False
    return bool(
        datasets.sources
        or datasets.filters
        or datasets.max_per_dataset != "default"
        or datasets.max_total != "default"
        or datasets.fetch is not None
    )


def scenario_accepts_dataset_config(scenario_class: type[Scenario]) -> bool:
    """
    Return whether a scenario declares ``dataset_config``.

    Returns:
        bool: True when the scenario still accepts a dataset configuration argument.
    """
    return any(parameter.name == "dataset_config" for parameter in scenario_class.supported_parameters())


def scenario_supports_exact_example_selection(scenario_class: type[Scenario]) -> bool:
    """
    Return whether launch can run this scenario on exact inline groups.

    Declaring ``dataset_config`` is not enough. Scenarios that read named datasets or a
    private configuration set ``SUPPORTS_EXACT_EXAMPLE_SELECTION`` to false.

    Returns:
        bool: True when an exact selection can replace the population.
    """
    return scenario_class.SUPPORTS_EXACT_EXAMPLE_SELECTION and scenario_accepts_dataset_config(scenario_class)


async def resolve_dataset_example_selection_async(
    *,
    selection: DatasetExampleSelection,
    memory: MemoryInterface | None = None,
) -> tuple[ResolvedDatasetExample, ...]:
    """
    Resolve exact references to stored records in request order.

    The read is scoped to each reference's dataset. Missing rows are not filled from
    another dataset, regenerated, or written back.

    Args:
        selection: Client references and the fingerprints they were shown.
        memory: Memory to read. Defaults to the central memory instance.

    Returns:
        tuple[ResolvedDatasetExample, ...]: One result per reference, in request order.

    Raises:
        DatasetExampleSelectionError: If a key is invalid, duplicated, missing, or stale.
    """
    reader = memory or CentralMemory.get_memory_instance()
    parsed: list[tuple[DatasetExampleReference, str | None]] = []
    seen: set[tuple[str | None, UUID]] = set()
    for reference in selection.examples:
        try:
            scope = parse_dataset_selection_key(reference.selection_key)
        except ValueError as exc:
            raise DatasetExampleSelectionError(
                code="invalid_key",
                selection_key=reference.selection_key,
                example_id=reference.example_id,
                detail="The selection key is not a dataset browse key.",
            ) from exc
        identity = (scope, reference.example_id)
        if identity in seen:
            raise DatasetExampleSelectionError(
                code="duplicate",
                selection_key=reference.selection_key,
                example_id=reference.example_id,
                detail="The same dataset example is selected more than once.",
            )
        seen.add(identity)
        parsed.append((reference, scope))

    ids_by_scope: dict[str | None, list[UUID]] = {}
    for reference, scope in parsed:
        ids_by_scope.setdefault(scope, []).append(reference.example_id)

    loaded: dict[tuple[str | None, UUID], list[SeedRecord]] = {}
    for scope, example_ids in ids_by_scope.items():
        found = await reader.get_seed_examples_by_ids_async(dataset_name=scope, example_ids=example_ids)
        for example_id, members in found.items():
            loaded[(scope, example_id)] = members

    resolved: list[ResolvedDatasetExample] = []
    for reference, scope in parsed:
        members = loaded.get((scope, reference.example_id))
        if not members:
            raise DatasetExampleSelectionError(
                code="missing",
                selection_key=reference.selection_key,
                example_id=reference.example_id,
                detail="The dataset does not contain this example.",
            )
        digest = dataset_example_fingerprint(
            selection_key=reference.selection_key, example_id=reference.example_id, members=members
        )
        if digest != reference.fingerprint:
            raise DatasetExampleSelectionError(
                code="stale",
                selection_key=reference.selection_key,
                example_id=reference.example_id,
                detail="The stored example changed. Refresh the dataset or choose the example again.",
            )
        resolved.append(ResolvedDatasetExample(reference=reference, members=tuple(members)))
    return tuple(resolved)


def exact_example_dataset_configuration(
    examples: Sequence[ResolvedDatasetExample],
) -> ExactExampleDatasetConfiguration:
    """
    Build the inline configuration for a resolved selection.

    Returns:
        ExactExampleDatasetConfiguration: Population of the resolved records, with sampling off.
    """
    return ExactExampleDatasetConfiguration(
        examples=tuple((example.reference, example.members) for example in examples)
    )
