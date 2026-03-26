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

from pyrit.identifiers import ComponentIdentifier
from pyrit.registry.instance_registries.base_instance_registry import (
    BaseInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.prompt_converter import PromptConverter

logger = logging.getLogger(__name__)


class ConverterRegistry(BaseInstanceRegistry["PromptConverter", ComponentIdentifier]):
    """
    Registry for managing available converter instances.

    This registry stores pre-configured PromptConverter instances (not classes).
    Converters are registered explicitly via initializers after being instantiated
    with their required parameters.
    """

    @classmethod
    def get_registry_singleton(cls) -> ConverterRegistry:
        """
        Get the singleton instance of the ConverterRegistry.

        Returns:
            The singleton ConverterRegistry instance.
        """
        return super().get_registry_singleton()  # type: ignore[return-value]

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

    def _build_metadata(self, name: str, instance: PromptConverter) -> ComponentIdentifier:
        """
        Build metadata for a converter instance.

        Args:
            name: The registry name of the converter.
            instance: The converter instance.

        Returns:
            ComponentIdentifier: The converter's identifier.
        """
        return instance.get_identifier()
