# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a seed's identifier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)

if TYPE_CHECKING:
    from pyrit.models.seeds.seed import Seed


class SeedIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``Seed``'s ``ComponentIdentifier``.

    Promotes the seed properties that define its identity: the raw value, its
    SHA256, the originating dataset, the data type, and whether it is a general
    technique.
    """

    #: The seed's raw value.
    value: Annotated[str | None, Evaluate.Include()] = None
    #: SHA256 of the seed value.
    value_sha256: Annotated[str | None, Evaluate.Include()] = None
    #: The seed's data type (e.g. ``"text"``, ``"image_path"``).
    data_type: Annotated[PromptDataType | None, Evaluate.Include()] = None
    #: Name of the dataset the seed came from.
    dataset_name: Annotated[str | None, Evaluate.Include()] = None
    #: Whether the seed represents a general (non-objective-specific) technique.
    is_general_technique: Annotated[bool | None, Evaluate.Include()] = None

    @classmethod
    def from_seed(cls, seed: Seed) -> SeedIdentifier:
        """
        Build a SeedIdentifier from a seed's behavioral properties.

        Captures the seed's content hash, dataset name, and class type so that
        different seeds produce different identifiers while the same seed content
        always produces the same identifier.

        Args:
            seed: The seed to build an identifier for.

        Returns:
            An identifier capturing the seed's behavioral properties.
        """
        return cls.of(
            seed,
            value=seed.value,
            value_sha256=seed.value_sha256,
            data_type=seed.data_type,
            dataset_name=seed.dataset_name,
            is_general_technique=seed.is_general_technique,
        )
