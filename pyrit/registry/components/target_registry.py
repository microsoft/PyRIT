# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target registry for PyRIT.

A single registry for ``PromptTarget`` that both:

- **builds** targets from a type name plus arguments — discovering target classes,
  deriving their ``Parameter`` contract from the constructor enriched by
  ``TargetIdentifier``'s build markers, and constructing instances via the shared
  resolver (so a multi-target such as ``RoundRobinTarget`` can be built by passing
  a list of ``targets`` registry names), and
- **holds** pre-configured target instances registered via initializers or the
  backend.

It is a ``Registry``: the registry's own surface (``get_class``,
``get_class_names``, ``get_all_registered_class_metadata``, ``create_instance``)
is the buildable class catalog. Pre-configured instances live under the
``instances`` property (``register``, ``get``, ``get_all_instances``,
``get_names``), a ``DefaultInstanceRegistry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models.identifiers import TargetIdentifier
from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry

if TYPE_CHECKING:
    from types import ModuleType

    from pyrit.prompt_target import PromptTarget


@dataclass(frozen=True)
class TargetMetadata(ClassRegistryEntry):
    """
    Metadata describing a registered ``PromptTarget`` class.

    Carries the derived ``parameters`` build contract (the same list the resolver
    consumes to build an instance). Use ``TargetRegistry.get_class()`` to get the
    actual class or ``create_instance()`` to build a configured instance.
    """


class TargetRegistry(Registry["PromptTarget", TargetMetadata]):
    """
    Registry that discovers, builds, and holds ``PromptTarget`` instances.

    Discovers all concrete ``PromptTarget`` subclasses exported from
    ``pyrit.prompt_target`` (keyed by their exact class name, e.g.
    ``"OpenAIChatTarget"``) for the buildable catalog. Pre-configured instances
    registered via initializers or the backend are held under the ``instances``
    property.

    Building a multi-target resolves its arguments through the shared resolver, so
    a ``RoundRobinTarget`` can be constructed by passing a list of ``targets`` that
    name targets already held under ``instances``.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry and its typed ``instances`` container.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)
        self.instances: InstanceRegistry[PromptTarget] = DefaultInstanceRegistry(instance_type=self._base_type)

    def _base_type(self) -> type[PromptTarget]:
        """Return the ``PromptTarget`` base class, imported lazily."""
        from pyrit.prompt_target import PromptTarget

        return PromptTarget

    def _discovery_package(self) -> ModuleType:
        """Return the ``pyrit.prompt_target`` package scanned for target classes."""
        from pyrit import prompt_target

        return prompt_target

    def _identifier_type(self) -> type[TargetIdentifier]:
        """Return ``TargetIdentifier`` so its ``Param.*`` markers drive derivation."""
        return TargetIdentifier

    def _metadata_class(self) -> type[TargetMetadata]:
        """Return ``TargetMetadata``; the base populates it from the common fields."""
        return TargetMetadata
