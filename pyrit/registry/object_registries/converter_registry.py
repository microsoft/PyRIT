# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter registry for managing PyRIT converter instances.

Converters are registered explicitly via initializers as pre-configured instances.

NOTE: This is a placeholder implementation. A full implementation will be added soon.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union

from pyrit.registry.object_registries.retrievable_instance_registry import (
    RetrievableInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.prompt_converter import PromptConverter

logger = logging.getLogger(__name__)


class ConverterRegistry(RetrievableInstanceRegistry["PromptConverter"]):
    """
    Registry for managing available converter instances.

    This registry stores pre-configured PromptConverter instances (not classes).
    Converters are registered explicitly via initializers after being instantiated
    with their required parameters.
    """

    def register_instance(
        self,
        converter: PromptConverter,
        *,
        name: Optional[str] = None,
        tags: Optional[Union[dict[str, str], list[str]]] = None,
    ) -> None:
        """
        Register a converter instance.

        Args:
            converter: The pre-configured converter instance (not a class).
            name: Optional custom registry name. If not provided,
                derived from the converter's unique identifier.
            tags: Optional tags for categorisation. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).
        """
        if name is None:
            name = converter.get_identifier().unique_name

        self.register(converter, name=name, tags=tags)
        logger.debug(f"Registered converter instance: {name} ({converter.__class__.__name__})")

    def get_instance_by_name(self, name: str) -> Optional[PromptConverter]:
        """
        Get a registered converter instance by name.

        Args:
            name: The registry name of the converter.

        Returns:
            The converter instance, or None if not found.
        """
        return self.get(name)
