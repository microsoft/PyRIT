# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Class-only registry for discovering and building attack strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models.identifiers import AttackIdentifier
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata

if TYPE_CHECKING:
    from types import ModuleType

    from pyrit.executor.attack import AttackStrategy


class AttackRegistry(Registry["AttackStrategy", RegistryMetadata]):
    """
    Discover concrete ``AttackStrategy`` classes and build configured attacks.

    Uses constructor metadata and the shared resolver. ``objective_target`` accepts
    a registered target name or a live target. Typed configuration objects pass
    through unchanged. Discovery and metadata do not construct attacks.

    This registry stores classes, not live attacks or attack technique factories.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """Enable declared configuration inputs before class discovery."""
        from pyrit.registry.technique_inputs import declare_technique_inputs

        declare_technique_inputs()
        super().__init__(lazy_discovery=lazy_discovery)

    def _base_type(self) -> type[AttackStrategy]:
        """Return the attack base class, imported lazily."""
        from pyrit.executor.attack import AttackStrategy

        return AttackStrategy

    def _discovery_package(self) -> ModuleType:
        """Return the package used for attack class discovery."""
        from pyrit.executor import attack

        return attack

    def _identifier_type(self) -> type[AttackIdentifier]:
        """Return the identifier that declares attack build references."""
        return AttackIdentifier

    def _metadata_class(self) -> type[RegistryMetadata]:
        """Return the shared constructor metadata type."""
        return RegistryMetadata
