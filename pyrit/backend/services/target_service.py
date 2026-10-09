# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target service for managing target instances.

Handles creation and retrieval of target instances.
Uses TargetRegistry as the source of truth for instances.

Targets can be:
- Created via API request (instantiated from request params, then registered)
- Retrieved from registry (pre-registered at startup or created earlier)
"""

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from threading import RLock
from typing import Any, Literal

from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetListResponse,
    TargetTypeEntry,
    TargetTypeResponse,
)
from pyrit.common import REQUIRED_VALUE
from pyrit.models import ComponentIdentifier, JSONValue, Parameter
from pyrit.models.catalog.target import TargetInstance
from pyrit.models.parameter import ComponentType
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry

logger = logging.getLogger(__name__)

_ENV_BACKED_REQUIRED_PARAMETERS: dict[str, frozenset[str]] = {
    "OpenAITarget": frozenset({"endpoint", "model_name"}),
    "AzureBlobStorageTarget": frozenset({"container_url"}),
    "AzureMLChatTarget": frozenset({"endpoint"}),
    "HackAPromptTarget": frozenset({"cookie", "session_id"}),
    "HuggingFaceChatTarget": frozenset({"hf_access_token"}),
    "PromptShieldTarget": frozenset({"endpoint"}),
}


class TargetDeletionConflictError(ValueError):
    """The target is still referenced by registered components or live work."""


class TargetDeletionProtectedError(ValueError):
    """Only entries explicitly created through the target API may be deleted."""


class TargetService:
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth for class discovery,
    parameter coercion, reference resolution, and construction. Endpoint
    validation remains owned by the target classes.
    """

    _MANUAL_ENTRY_KEY = "created_by_target_api"

    def __init__(self) -> None:
        """Initialize the target service."""
        self._registry = TargetRegistry.get_registry_singleton()
        self._usage: Counter[str] = Counter()
        self._usage_lock = RLock()

    def _build_instance_from_object(self, *, target_registry_name: str, target_obj: Any) -> TargetInstance:
        """
        Build a TargetInstance from a registry object.

        Returns:
            TargetInstance with metadata derived from the object.
        """
        target = target_object_to_instance(target_registry_name, target_obj)
        entry = self._registry.instances.get_entry(target_registry_name)
        target.deletion_blocked_reason = self._deletion_blocked_reason(
            entry.metadata if entry and entry.instance is target_obj else {}
        )
        target.can_delete = target.deletion_blocked_reason is None
        return target

    @classmethod
    def _deletion_blocked_reason(cls, metadata: dict[str, Any]) -> str | None:
        """
        Explain source ownership without inferring it from target names or classes.

        Returns:
            str | None: An actionable explanation, or None for a manually created entry.
        """
        origin = metadata.get("target_origin")
        if origin == "configuration":
            return (
                "This target is generated automatically from your .env configuration and cannot be deleted here. "
                "To remove it, update .env or the target initializers in .pyrit_conf, then reinitialize."
            )
        if origin == "auto_generated":
            return (
                "This target is generated automatically from your configured targets and cannot be deleted here. "
                "To remove it, update the source targets in .env or the target initializers in .pyrit_conf, "
                "then reinitialize."
            )
        if metadata.get(cls._MANUAL_ENTRY_KEY) is True:
            return None
        return (
            "Only manually added targets can be deleted. This entry has no user-created origin recorded. "
            "For managed targets, edit .env / .pyrit_conf and reinitialize instead."
        )

    @contextmanager
    def reserve_targets(self, names: Sequence[str | None]) -> Iterator[None]:
        """Prevent deletion while accepted work owns these registry names, including queued work."""
        reserved = {name for name in names if name is not None}
        with self._usage_lock:
            self._usage.update(reserved)
        try:
            yield
        finally:
            with self._usage_lock:
                self._usage.subtract(reserved)
                self._usage += Counter()

    @contextmanager
    def reserve_identifiers(self, identifiers: Sequence[ComponentIdentifier]) -> Iterator[None]:
        """Retain target dependencies even if their owning converter is unregistered during execution."""
        with ExitStack() as reservation:
            with self._usage_lock:
                names = [
                    entry.name
                    for entry in self._registry.instances.get_all_instances()
                    if any(
                        identifier.hash == entry.instance.get_identifier().hash
                        or self._references_target(
                            identifier=identifier, target_hash=entry.instance.get_identifier().hash
                        )
                        for identifier in identifiers
                    )
                ]
                reservation.enter_context(self.reserve_targets(names))
            yield

    @contextmanager
    def reserve_parameter_targets(
        self, *, parameters: Sequence[Parameter], values: dict[str, JSONValue]
    ) -> Iterator[None]:
        """Protect declared target references before threaded component construction resolves them."""
        names: list[str] = []
        for parameter in parameters:
            if parameter.is_reference_to(ComponentType.TARGET):
                value = values.get(parameter.name)
                names.extend(item for item in (value if isinstance(value, list) else [value]) if isinstance(item, str))
        with self.reserve_targets(names):
            yield

    async def delete_target_async(self, *, target_registry_name: str) -> bool:
        """
        Unregister an unused, manually created target without altering history.

        Returns:
            bool: Whether the entry existed and was removed.

        Raises:
            TargetDeletionProtectedError: If the entry has no explicit manual origin.
            TargetDeletionConflictError: If live work or another component references it.
        """
        with self._usage_lock:
            entry = self._registry.instances.get_entry(target_registry_name)
            if entry is None:
                return False
            blocked_reason = self._deletion_blocked_reason(entry.metadata)
            if blocked_reason is not None:
                raise TargetDeletionProtectedError(blocked_reason)
            if self._usage[target_registry_name]:
                raise TargetDeletionConflictError(
                    f"Target '{target_registry_name}' is in use by active or queued work. Wait for it to finish."
                )
            target_hash = entry.instance.get_identifier().hash
            for label, instances in (
                ("target", self._registry.instances),
                ("converter", ConverterRegistry.get_registry_singleton().instances),
                ("scorer", ScorerRegistry.get_registry_singleton().instances),
            ):
                for dependent in instances.get_all_instances():
                    if dependent is entry:
                        continue
                    if self._references_target(identifier=dependent.instance.get_identifier(), target_hash=target_hash):
                        raise TargetDeletionConflictError(
                            f"Target '{target_registry_name}' is used by {label} '{dependent.name}'. "
                            "Remove that dependency first."
                        )
            for technique in AttackTechniqueRegistry.get_registry_singleton().instances.get_all_instances():
                target = technique.instance.adversarial_chat
                if target is not None and (
                    target.get_identifier().hash == target_hash
                    or self._references_target(identifier=target.get_identifier(), target_hash=target_hash)
                ):
                    raise TargetDeletionConflictError(
                        f"Target '{target_registry_name}' is used by attack technique '{technique.name}'. "
                        "Remove that dependency first."
                    )
            if self._registry.instances.unregister(target_registry_name, expected_entry=entry) is None:
                raise TargetDeletionConflictError("The target registration changed. Refresh targets and retry.")
            return True

    @classmethod
    def _references_target(cls, *, identifier: ComponentIdentifier, target_hash: str) -> bool:
        """
        Match full identities in nested dependencies, not broader evaluation identities.

        Returns:
            bool: Whether a child at any depth has the target's full identity.
        """
        for children in identifier.children.values():
            for child in children if isinstance(children, list) else [children]:
                if child.hash == target_hash or cls._references_target(identifier=child, target_hash=target_hash):
                    return True
        return False

    async def list_targets_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TargetListResponse:
        """
        List all target instances with pagination.

        Args:
            limit: Maximum items to return.
            cursor: Pagination cursor (target_registry_name to start after).

        Returns:
            TargetListResponse containing paginated targets.
        """
        items = [
            self._build_instance_from_object(target_registry_name=entry.name, target_obj=entry.instance)
            for entry in self._registry.instances.get_all_instances()
        ]
        page, has_more = self._paginate(items=items, cursor=cursor, limit=limit)
        next_cursor = page[-1].target_registry_name if has_more and page else None
        return TargetListResponse(
            items=page,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
        )

    @staticmethod
    def _paginate(*, items: list[TargetInstance], cursor: str | None, limit: int) -> tuple[list[TargetInstance], bool]:
        """
        Apply cursor-based pagination.

        Returns:
            Tuple of (paginated items, has_more flag).
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.target_registry_name == cursor:
                    start_idx = i + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more

    async def get_target_async(self, *, target_registry_name: str) -> TargetInstance | None:
        """
        Get a target instance by registry name.

        Returns:
            TargetInstance if found, None otherwise.
        """
        obj = self._registry.instances.get(target_registry_name)
        if obj is None:
            return None
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=obj)

    def get_target_object(self, *, target_registry_name: str) -> Any | None:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.instances.get(target_registry_name)

    @staticmethod
    def _get_supported_auth_modes(auth_modes: tuple[str, ...]) -> list[Literal["api_key", "identity"]]:
        """
        Validate and narrow registry authentication modes for the type response.

        Args:
            auth_modes (tuple[str, ...]): Authentication modes declared by a target class.

        Returns:
            list[Literal["api_key", "identity"]]: Validated authentication modes.

        Raises:
            ValueError: If a target class declares an unsupported authentication mode.
        """
        supported_auth_modes: list[Literal["api_key", "identity"]] = []
        for auth_mode in auth_modes:
            if auth_mode == "api_key" or auth_mode == "identity":
                supported_auth_modes.append(auth_mode)
                continue
            raise ValueError(f"Unsupported target authentication mode: {auth_mode!r}")
        return supported_auth_modes

    def _project_target_parameters(self, *, target_type: str, parameters: tuple[Parameter, ...]) -> list[Parameter]:
        """
        Project registry parameters into the API contract.

        Environment-backed values remain optional in Python constructors so targets
        can resolve them from dotenv configuration. The GUI must still collect them
        explicitly, so the API marks those values required without changing the
        target constructor signatures.

        Args:
            target_type (str): Registered target class name.
            parameters (tuple[Parameter, ...]): Constructor parameters derived by the registry.

        Returns:
            list[Parameter]: Parameters projected for dynamic form generation.
        """
        target_cls = self._registry.get_class(target_type)
        required_names = frozenset().union(
            *(_ENV_BACKED_REQUIRED_PARAMETERS.get(base.__name__, frozenset()) for base in target_cls.__mro__)
        )
        return [
            parameter.model_copy(update={"default": REQUIRED_VALUE}) if parameter.name in required_names else parameter
            for parameter in parameters
        ]

    async def list_target_types_async(self) -> TargetTypeResponse:
        """
        List all available target types from the target class registry.

        Returns every constructible target with its derived constructor
        parameters and the auth modes it supports, all projected from the
        registry's ``TargetMetadata``. Deciding which entries to surface to a
        user is a presentation concern owned by the caller (e.g. the frontend),
        not this service.

        Returns:
            TargetTypeResponse containing all available target classes.
        """
        metadata_items = await asyncio.to_thread(self._registry.get_all_registered_class_metadata)
        items: list[TargetTypeEntry] = [
            TargetTypeEntry(
                target_type=metadata.class_name,
                parameters=self._project_target_parameters(
                    target_type=metadata.class_name,
                    parameters=metadata.parameters,
                ),
                supported_auth_modes=self._get_supported_auth_modes(metadata.supported_auth_modes),
                description=metadata.class_description or None,
            )
            for metadata in metadata_items
        ]
        return TargetTypeResponse(items=items)

    async def create_target_async(self, *, request: CreateTargetRequest) -> TargetInstance:
        """
        Create a new target instance from API request.

        Class discovery, strict parameter validation, scalar coercion, registry
        reference resolution, and construction are owned by the
        ``TargetRegistry``. Endpoint trust and identity token minting are owned
        by the target classes themselves. This service only enforces the
        request-level auth contract: for ``identity`` it confirms the target
        supports it and omits the api_key plus any registry-flagged
        identity-conflicting parameters so the target validates its own
        endpoint and authenticates itself. The response is built before the
        target is registered, so a failed request leaves no registered target.

        Args:
            request: The create target request with type, params, and auth_mode.

        Returns:
            TargetInstance with the new target's details.

        Raises:
            ValueError: If the target type is not registered or identity auth is
                requested but unsupported by the target type. Construction errors
                (unknown params, incompatible inner targets, unrecognized identity
                endpoints) are raised by the registry / target classes.
        """
        if request.type not in self._registry:
            raise ValueError(
                f"Target type '{request.type}' not found. Available types: {self._registry.get_class_names()}"
            )

        target_cls = self._registry.get_class(request.type)
        params: dict[str, Any] = dict(request.params)

        if request.auth_mode == "identity":
            if "identity" not in target_cls.supported_auth_modes:
                raise ValueError(f"Target type '{request.type}' does not support identity-based authentication.")
            # Omit any api_key so the target validates its own endpoint and authenticates itself.
            params.pop("api_key", None)
            # Omit any other parameter the registry metadata marks as conflicting with
            # identity-based auth (e.g. AzureBlobStorageTarget's sas_token), so a caller
            # can't silently override the selected auth mode by also supplying it.
            metadata = await asyncio.to_thread(self._registry.get_registered_class_metadata, request.type)
            if metadata is not None:
                for parameter in metadata.parameters:
                    if parameter.identity_conflicting:
                        params.pop(parameter.name, None)
        params.update(target_cls.get_auth_mode_parameters(auth_mode=request.auth_mode))

        # LEGACY COMPATIBILITY: The current configuration UI omits the name.
        # Remove this generated fallback after that UI sends an explicit name.
        target_registry_name = request.name or f"compat_{uuid.uuid4().hex}"
        self._registry.instances.validate_name_available(target_registry_name)
        target_obj = self._registry.create_instance(request.type, **params)
        target = self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target_obj)
        self._registry.instances.register(
            target_obj, name=target_registry_name, metadata={self._MANUAL_ENTRY_KEY: True}
        )
        target.can_delete = True
        target.deletion_blocked_reason = None
        return target


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
