# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a seed's identifier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pyrit.models.identifiers.component_identifier import ComponentIdentifier, config_hash
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.seeds.seed import Seed


class SeedIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``Seed``'s ``ComponentIdentifier``.

    Promotes the seed properties that define its identity: the raw value, its
    SHA256, the originating dataset, the data type, and whether it is a general
    technique. Objective conditions and named adversarial prompts are retained
    in the full identifier JSON. Seeds without them retain their existing identity.
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
    def from_seed(cls, seed: Seed[Any]) -> SeedIdentifier:
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
            params=cls.get_behavioral_parameters(seed) or None,
            value=seed.value,
            value_sha256=seed.value_sha256,
            data_type=seed.data_type,
            dataset_name=seed.dataset_name,
            is_general_technique=seed.is_general_technique,
        )

    @staticmethod
    def get_behavioral_parameters(seed: Seed[Any]) -> dict[str, Any]:
        """
        Project conditions and named prompts for seed identity and storage.

        Args:
            seed (Seed): The seed whose behavioral inputs are projected.

        Returns:
            dict[str, Any]: Canonical inputs, excluding descriptive metadata and prompt IDs.
        """
        from pyrit.models.seeds.adversarial_prompts import AdversarialPrompts

        conditions = seed.model_dump(mode="json", include={"conditions"}).get("conditions")
        params = {"conditions": conditions} if conditions else {}
        adversarial_prompts = AdversarialPrompts.from_metadata(seed.metadata)
        if adversarial_prompts is not None:
            params["adversarial_prompts"] = adversarial_prompts.identity
        return params


def compute_seed_group_hash(seed_identifiers: Sequence[SeedIdentifier]) -> str:
    """Return the deterministic hash of ordered canonical seed identifiers."""
    return config_hash(
        {
            "seed_identifiers": [
                seed_identifier.model_dump(exclude={"hash", "eval_hash", "pyrit_version"})
                for seed_identifier in seed_identifiers
            ]
        }
    )
