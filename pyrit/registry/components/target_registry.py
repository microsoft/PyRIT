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

from pyrit.models import TargetIdentifier
from pyrit.registry.registry import InstanceHoldingRegistry
from pyrit.registry.registry_metadata import RegistryMetadata
from pyrit.registry.resolution import resolve_constructor_args

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

    from pyrit.prompt_target import PromptTarget


@dataclass(frozen=True)
class TargetMetadata(RegistryMetadata):
    """
    Metadata describing a registered ``PromptTarget`` class.

    Carries the derived ``parameters`` build contract (the same list the resolver
    consumes to build an instance) and, via ``class_attributes`` on the base, the
    target's declarative auth facts. ``supported_auth_modes`` is projected from
    those rather than stored, so the entry can never drift from the class. Use
    ``TargetRegistry.get_class()`` to get the actual class or ``create_instance()``
    to build a configured instance.
    """

    @property
    def supported_auth_modes(self) -> tuple[str, ...]:
        """Auth modes this target type accepts (e.g. ``"api_key"``, ``"identity"``)."""
        auth_modes: tuple[str, ...] = tuple(self.class_attributes.get("supported_auth_modes") or ())
        return auth_modes


class TargetRegistry(InstanceHoldingRegistry["PromptTarget", TargetMetadata]):
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
        super().__init__(
            lazy_discovery=lazy_discovery,
            reserved_instance_names={"types"},
        )

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

    async def create_instance_async(
        self,
        *,
        name: str,
        params: Mapping[str, object],
        external_input: bool = False,
        allowed_path_params: frozenset[str] = frozenset(),
    ) -> PromptTarget:
        """
        Build a target through its ``create_async``, so slow setup it needs runs off the calling event loop.

        The arguments are resolved as ``create_instance`` resolves them or, with ``external_input``, as
        ``create_instance_from_external_input`` does, so the target gets the values its constructor will.

        Args:
            name (str): The catalog name to build.
            params (Mapping[str, object]): The constructor arguments.
            external_input (bool): Whether ``params`` come from an external caller such as a REST request.
                Defaults to False.
            allowed_path_params (frozenset[str]): External parameters that can name a server file or folder but
                that the caller has made safe. Defaults to none.

        Returns:
            PromptTarget: The new target, not registered.

        Raises:
            KeyError: If the name is not registered.
            ValueError: If an argument can't be resolved or coerced for the target's constructor.
        """
        cls = self.get_class(name)
        resolved = resolve_constructor_args(
            cls=cls,
            raw_args=dict(params),
            identifier_type=self._identifier_type(),
            external_input=external_input,
            seam_base_type=self._seam_base_type(),
            allowed_path_params=allowed_path_params,
        )
        return await cls.create_async(**resolved)
