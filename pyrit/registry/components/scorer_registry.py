# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer registry for PyRIT.

A single registry for ``Scorer`` that both:

- **builds** scorers from a type name plus arguments — discovering scorer classes,
  deriving their ``Parameter`` contract from the constructor enriched by
  ``ScorerIdentifier``'s build markers, and constructing instances via the shared
  resolver (so an LLM scorer can be built by passing a ``chat_target`` registry
  name, and a composite scorer by passing a list of ``scorers`` registry names),
  and
- **holds** pre-configured scorer instances registered via initializers or the
  backend.

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
from typing import TYPE_CHECKING

from pyrit.models.identifiers import ScorerIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry

if TYPE_CHECKING:
    from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


def _scorer_type() -> type[Scorer]:
    """
    Return the ``Scorer`` base class, importing it lazily.

    Used as the ``instance_type`` for the registry's ``instances`` container so a
    non-scorer cannot be registered, without importing the score package at module
    load (which would defeat lazy discovery).

    Returns:
        type[Scorer]: The ``Scorer`` base class.
    """
    from pyrit.score.scorer import Scorer

    return Scorer


@dataclass(frozen=True)
class ScorerMetadata(ClassRegistryEntry):
    """
    Metadata describing a registered ``Scorer`` class.

    Carries the derived ``parameters`` build contract (the same list the resolver
    consumes to build an instance). Whether the scorer is LLM-based is projected
    from that contract rather than stored, so the entry can never drift from the
    class.

    Use ``ScorerRegistry.get_class()`` to get the actual class or
    ``create_instance()`` to build a configured instance.
    """

    @property
    def is_llm_based(self) -> bool:
        """Whether the scorer requires an LLM target (a TARGET reference parameter)."""
        return any(p.is_reference_to(ComponentType.TARGET) for p in self.parameters)


class ScorerRegistry(Registry["Scorer", ScorerMetadata]):
    """
    Registry that discovers, builds, and holds ``Scorer`` instances.

    Discovers all concrete ``Scorer`` subclasses exported from ``pyrit.score``
    (keyed by their exact class name, e.g. ``"SelfAskRefusalScorer"``) for the
    buildable catalog. Pre-configured instances registered via initializers or the
    backend are held under the ``instances`` property.

    Building a scorer resolves its arguments through the shared resolver, so LLM
    scorers can be constructed by passing a ``chat_target`` that names a target in
    the ``TargetRegistry``, and composite scorers by passing a list of ``scorers``
    that name scorers already held under ``instances``.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)
        self.instances: InstanceRegistry[Scorer] = DefaultInstanceRegistry(instance_type=_scorer_type)

    def _identifier_type(self) -> type[ScorerIdentifier]:
        """Return ``ScorerIdentifier`` so its ``Param.*`` markers drive derivation."""
        return ScorerIdentifier

    def _get_registry_name(self, cls: type[Scorer]) -> str:
        """
        Use the exact class name as the catalog key.

        Scorers are referenced by their class name (e.g. ``"SelfAskRefusalScorer"``)
        rather than the snake_case default used by other class registries.

        Returns:
            str: The class name.
        """
        return cls.__name__

    def _discover(self) -> None:
        """Discover all concrete ``Scorer`` subclasses from ``pyrit.score``."""
        from pyrit import score
        from pyrit.score.scorer import Scorer

        for name in score.__all__:
            cls = getattr(score, name, None)
            if cls is None or not isinstance(cls, type):
                continue
            if not issubclass(cls, Scorer) or cls is Scorer or inspect.isabstract(cls):
                continue
            self.register_class(cls)
            logger.debug(f"Registered scorer class: {cls.__name__}")

    def _metadata_class(self) -> type[ScorerMetadata]:
        """Return ``ScorerMetadata``; the base populates it from the common fields."""
        return ScorerMetadata
