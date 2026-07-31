# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter registry for PyRIT.

A single registry for ``Converter`` that both:

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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models.identifiers import ConverterIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata

if TYPE_CHECKING:
    from types import ModuleType

    from pyrit.converter import Converter


@dataclass(frozen=True)
class ConverterMetadata(RegistryMetadata):
    """
    Metadata describing a registered ``Converter`` class.

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


class ConverterRegistry(Registry["Converter", ConverterMetadata]):
    """
    Registry that discovers, builds, and holds ``Converter`` instances.

    Discovers all concrete ``Converter`` subclasses exported from
    ``pyrit.converter`` (keyed by their exact class name, e.g.
    ``"Base64Converter"``) for the buildable catalog. Pre-configured instances
    registered via initializers or the backend are held under the ``instances``
    property.

    Building a converter resolves its arguments through the shared resolver, so
    LLM converters can be constructed by passing a ``converter_target`` that names
    a target in the ``TargetRegistry``.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry and its typed ``instances`` container.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)
        self.instances: InstanceRegistry[Converter] = DefaultInstanceRegistry(instance_type=self._base_type)

    def _base_type(self) -> type[Converter]:
        """Return the ``Converter`` base class, imported lazily."""
        from pyrit.converter import Converter

        return Converter

    def _discovery_package(self) -> ModuleType:
        """Return the ``pyrit.converter`` package scanned for converter classes."""
        from pyrit import converter

        return converter

    def _identifier_type(self) -> type[ConverterIdentifier]:
        """Return ``ConverterIdentifier`` so its ``Param.*`` markers drive derivation."""
        return ConverterIdentifier

    def _metadata_class(self) -> type[ConverterMetadata]:
        """Return ``ConverterMetadata``; the base populates it from the common fields."""
        return ConverterMetadata
