# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer registry for discovering and managing PyRIT scorers.

Scorers are registered explicitly via initializers as pre-configured instances.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union

from pyrit.registry.object_registries.retrievable_instance_registry import (
    RetrievableInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


class ScorerRegistry(RetrievableInstanceRegistry["Scorer"]):
    """
    Registry for managing available scorer instances.

    This registry stores pre-configured Scorer instances (not classes).
    Scorers are registered explicitly via initializers after being instantiated
    with their required parameters (e.g., chat_target).

    Scorers are identified by their snake_case name derived from the class name,
    or a custom name provided during registration.
    """

    def register_instance(
        self,
        scorer: Scorer,
        *,
        name: Optional[str] = None,
        tags: Optional[Union[dict[str, str], list[str]]] = None,
    ) -> None:
        """
        Register a scorer instance.

        Note: Unlike ScenarioRegistry and InitializerRegistry which register classes,
        ScorerRegistry registers pre-configured instances.

        Args:
            scorer: The pre-configured scorer instance (not a class).
            name: Optional custom registry name. If not provided,
                derived from the scorer's unique identifier.
            tags: Optional tags for categorisation. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).
        """
        if name is None:
            name = scorer.get_identifier().unique_name

        self.register(scorer, name=name, tags=tags)
        logger.debug(f"Registered scorer instance: {name} ({scorer.__class__.__name__})")

    def get_instance_by_name(self, name: str) -> Optional[Scorer]:
        """
        Get a registered scorer instance by name.

        Note: This returns an already-instantiated scorer, not a class.

        Args:
            name: The registry name of the scorer.

        Returns:
            The scorer instance, or None if not found.
        """
        return self.get(name)
