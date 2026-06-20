# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter registry for PyRIT.

A single registry for ``PromptConverter`` that both:

- **builds** converters from a type name plus arguments — discovering converter
  classes, deriving their ``Parameter`` contract from the constructor enriched by
  ``ConverterIdentifier``'s build markers, and constructing instances via the
  shared resolver (so LLM converters can be built by passing a ``converter_target``
  registry name), and
- **holds** pre-configured converter instances registered via initializers or the
  backend.

It is a ``Registry``: the registry's own surface (``get_class``,
``get_class_names``, ``get_all_registered_class_metadata``, ``create_instance``)
is the buildable class catalog. Pre-configured instances live under the
``instances`` property (``register``, ``get``, ``get_all_instances``,
``get_names``), a ``DefaultInstanceRegistry``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models.identifiers import ConverterIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry

if TYPE_CHECKING:
    from pyrit.prompt_converter import PromptConverter

logger = logging.getLogger(__name__)


def _prompt_converter_type() -> type[PromptConverter]:
    """
    Return the ``PromptConverter`` base class, importing it lazily.

    Used as the ``instance_type`` for the registry's ``instances`` container so
    a non-converter cannot be registered, without importing the converter
    package at module load (which would defeat lazy discovery).

    Returns:
        type[PromptConverter]: The ``PromptConverter`` base class.
    """
    from pyrit.prompt_converter import PromptConverter

    return PromptConverter


@dataclass(frozen=True)
class ConverterMetadata(ClassRegistryEntry):
    """
    Metadata describing a registered ``PromptConverter`` class.

    Carries the derived ``parameters`` build contract (the same list the resolver
    consumes to build an instance) and, via ``class_attributes`` on the base, the
    converter's class-level supported input/output types. Presentation facts — the
    supported types and whether the converter is LLM-based — are projected from
    those rather than stored, so the entry can never drift from the class or the
    contract.

    Use ``ConverterRegistry.get_class()`` to get the actual class or
    ``create_instance()`` to build a configured instance.
    """

    @property
    def supported_input_types(self) -> tuple[str, ...]:
        """Input data types the converter accepts (stringified ``PromptDataType`` values)."""
        return tuple(str(dt) for dt in (self.class_attributes.get("supported_input_types") or ()))

    @property
    def supported_output_types(self) -> tuple[str, ...]:
        """Output data types the converter produces (stringified ``PromptDataType`` values)."""
        return tuple(str(dt) for dt in (self.class_attributes.get("supported_output_types") or ()))

    @property
    def is_llm_based(self) -> bool:
        """Whether the converter requires an LLM target (a TARGET reference parameter)."""
        return any(p.is_reference_to(ComponentType.TARGET) for p in self.parameters)


class ConverterRegistry(Registry["PromptConverter", ConverterMetadata]):
    """
    Registry that discovers, builds, and holds ``PromptConverter`` instances.

    Discovers all concrete ``PromptConverter`` subclasses exported from
    ``pyrit.prompt_converter`` (keyed by their exact class name, e.g.
    ``"Base64Converter"``) for the buildable catalog. Pre-configured instances
    registered via initializers or the backend are held under the ``instances``
    property.

    Building a converter resolves its arguments through the shared resolver, so
    LLM converters can be constructed by passing a ``converter_target`` that names
    a target in the ``TargetRegistry``.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)
        self.instances: InstanceRegistry[PromptConverter] = DefaultInstanceRegistry(
            instance_type=_prompt_converter_type
        )

    def _identifier_type(self) -> type[ConverterIdentifier]:
        """Return ``ConverterIdentifier`` so its ``Param.*`` markers drive derivation."""
        return ConverterIdentifier

    def _get_registry_name(self, cls: type[PromptConverter]) -> str:
        """
        Use the exact class name as the catalog key.

        Converters are referenced by their class name (e.g. ``"Base64Converter"``)
        rather than the snake_case default used by other class registries.

        Returns:
            str: The class name.
        """
        return cls.__name__

    def _discover(self) -> None:
        """Discover all concrete ``PromptConverter`` subclasses from ``pyrit.prompt_converter``."""
        from pyrit import prompt_converter
        from pyrit.prompt_converter import PromptConverter

        for name in prompt_converter.__all__:
            cls = getattr(prompt_converter, name, None)
            if cls is None or not isinstance(cls, type):
                continue
            if not issubclass(cls, PromptConverter) or cls is PromptConverter:
                continue
            # Key off the class itself (via _get_registry_name) rather than the
            # __all__ export name so the catalog key always matches class_name,
            # even if an export is ever aliased.
            self.register_class(cls)
            logger.debug(f"Registered converter class: {cls.__name__}")

    def _metadata_class(self) -> type[ConverterMetadata]:
        """Return ``ConverterMetadata``; the base populates it from the common fields."""
        return ConverterMetadata
