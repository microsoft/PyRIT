# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Source registry for PyRIT.

A single registry for ``ObservationSource`` that both:

- **builds** sources from a type name plus arguments, discovering the source
  classes exported from ``pyrit.score.observation`` and deriving their
  ``Parameter`` contract from the constructor, and
- **holds** pre-configured source instances registered via initializers or the
  backend, so a scorer can be built by passing a ``source`` registry name.

Sources get their own namespace rather than joining the scorer namespace. A
source is configured independently of the scorer that consumes it, one source
can serve several scorers, and a source is not a scorer, so keeping it out of
the scorer catalog keeps that catalog limited to classes that score.

It is a ``Registry``: the registry's own surface (``get_class``,
``get_class_names``, ``get_all_registered_class_metadata``, ``create_instance``)
is the buildable class catalog. Pre-configured instances live under the
``instances`` property (``register``, ``get``, ``get_all_instances``,
``get_names``), a ``DefaultInstanceRegistry``.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.registry.registry import InstanceHoldingRegistry
from pyrit.registry.registry_metadata import RegistryMetadata

if TYPE_CHECKING:
    from types import ModuleType

    from pyrit.score.observation.observation_source import ObservationSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceMetadata(RegistryMetadata):
    """
    Metadata describing a registered ``ObservationSource`` class.

    Carries the derived ``parameters`` build contract (the same list the resolver
    consumes to build an instance). Use ``SourceRegistry.get_class()`` to get the
    actual class or ``create_instance()`` to build a configured instance.
    """


class SourceRegistry(InstanceHoldingRegistry["ObservationSource[Any]", SourceMetadata]):
    """
    Registry that discovers, builds, and holds ``ObservationSource`` instances.

    ``ObservationSource`` is a protocol, so sources share no base class to
    enumerate. Discovery therefore reads the names ``pyrit.score.observation``
    exports and keeps the concrete classes that satisfy the protocol (keyed by
    their exact class name, e.g. ``"OtelTraceSource"``). Pre-configured instances
    registered via initializers or the backend are held under the ``instances``
    property, and scorers resolve a ``source`` given by name from there.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry and its typed ``instances`` container.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)

    def _base_type(self) -> type[ObservationSource[Any]]:
        """Return the runtime-checkable ``ObservationSource`` protocol, imported lazily."""
        from pyrit.score.observation.observation_source import ObservationSource

        return ObservationSource

    def _discovery_package(self) -> ModuleType:
        """Return the ``pyrit.score.observation`` package scanned for source classes."""
        from pyrit.score import observation

        return observation

    def _discover(self) -> None:
        """
        Register every concrete source class exported from the discovery package.

        Sources implement ``ObservationSource`` structurally, so the subclass walk
        the base registry uses would find nothing. Each exported name is resolved
        instead, and kept when it is a class that satisfies the protocol and
        passes ``_should_register_discovered_class``. An export that fails to
        import (for example because of a missing optional dependency) is skipped.
        """
        package = self._discovery_package()
        base = self._base_type()
        for exported_name in getattr(package, "__all__", ()):
            try:
                exported = getattr(package, exported_name)
            except Exception as exc:
                logger.debug(f"Skipping lazily-exported '{exported_name}': {exc}")
                continue
            if not inspect.isclass(exported) or not issubclass(exported, base):
                continue
            if not self._should_register_discovered_class(exported):
                logger.debug(f"Skipping non-buildable source class: {exported.__name__}")
                continue
            name = self._get_registry_name(exported)
            if name in self._classes:
                continue
            self.register_class(exported, name=name)
            logger.debug(f"Registered ObservationSource class: {name}")

    def _should_register_discovered_class(self, cls: type[ObservationSource[Any]]) -> bool:
        """
        Exclude protocols, abstract classes, and private classes from the catalog.

        Args:
            cls (type[ObservationSource[Any]]): The discovered source class.

        Returns:
            bool: True for public, concrete source classes.
        """
        if getattr(cls, "_is_protocol", False) or inspect.isabstract(cls):
            return False
        return not cls.__name__.startswith("_")

    def _metadata_class(self) -> type[SourceMetadata]:
        """Return ``SourceMetadata``; the base populates it from the common fields."""
        return SourceMetadata
