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

import logging
import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from pyrit import prompt_target
from pyrit.auth import get_azure_async_token_provider, get_azure_openai_auth
from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetInstance,
    TargetListResponse,
)
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.azure_ml_chat_target import AzureMLChatTarget
from pyrit.prompt_target.openai.openai_target import OpenAITarget
from pyrit.prompt_target.round_robin_target import RoundRobinTarget
from pyrit.registry.object_registries import TargetRegistry

logger = logging.getLogger(__name__)

# Scope for Azure Machine Learning managed online endpoints.
_AZURE_ML_SCOPE = "https://ml.azure.com/.default"

# Recognised Azure OpenAI / AI Foundry hostname suffixes. Used for strict
# endpoint validation when Entra ID auth is requested, so a bearer token is
# only ever issued for a known Microsoft-operated endpoint.
_AZURE_OPENAI_HOSTNAME_SUFFIXES = (
    ".openai.azure.com",
    ".ai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
)

# Recognised Azure Machine Learning managed online endpoint hostname suffixes.
# Used for the same strict endpoint validation when issuing Entra ID tokens
# against an AML scope.
_AZURE_ML_HOSTNAME_SUFFIXES = (".inference.ml.azure.com",)


def _is_azure_openai_endpoint(endpoint: str) -> bool:
    """
    Return True if ``endpoint`` resolves to a known Azure OpenAI / AI Foundry host.
    Uses a strict hostname-suffix check (not a substring search).

    Args:
        endpoint (str): The endpoint URL to validate.

    Returns:
        bool: True if the endpoint's hostname ends with a recognised Azure suffix;
            False otherwise
    """
    hostname = (urlparse(endpoint).hostname or "").lower()
    return any(hostname.endswith(suffix) for suffix in _AZURE_OPENAI_HOSTNAME_SUFFIXES)


def _is_azure_ml_endpoint(endpoint: str) -> bool:
    """
    Return True if ``endpoint`` resolves to a known AML managed host.
    Uses a strict hostname-suffix check (not a substring search).

    Args:
        endpoint (str): The endpoint URL to validate.

    Returns:
        bool: True if the endpoint's hostname ends with a recognised AML suffix;
            False otherwise.
    """
    hostname = (urlparse(endpoint).hostname or "").lower()
    return any(hostname.endswith(suffix) for suffix in _AZURE_ML_HOSTNAME_SUFFIXES)


def _resolve_api_key_env_var(target_class: type) -> str | None:
    """
    Return the api_key environment variable name for a target class.

    Args:
        target_class (type): The target class to inspect.

    Returns:
        str | None: The env var name, or None if the class does not declare one.
    """
    if issubclass(target_class, AzureMLChatTarget):
        env_var = getattr(target_class, "api_key_environment_variable", None)
        return env_var if isinstance(env_var, str) and env_var else None
    if issubclass(target_class, OpenAITarget):
        try:
            instance = target_class.__new__(target_class)
            instance._set_openai_env_configuration_vars()
        except Exception:
            return None
        env_var = getattr(instance, "api_key_environment_variable", None)
        return env_var if isinstance(env_var, str) and env_var else None
    return None


def _build_target_class_registry() -> dict[str, type]:
    """
    Build a registry mapping target class names to their classes.

    Uses the prompt_target module's __all__ to discover all available targets.

    Returns:
        Dict mapping class name (str) to class (type).
    """
    registry: dict[str, type] = {}
    for name in prompt_target.__all__:
        cls = getattr(prompt_target, name, None)
        if cls is not None and isinstance(cls, type) and issubclass(cls, PromptTarget):
            registry[name] = cls
    return registry


# Module-level class registry (built once on import)
_TARGET_CLASS_REGISTRY: dict[str, type] = _build_target_class_registry()


class TargetService:
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth.
    API metadata is derived from the target objects' identifiers.
    """

    def __init__(self) -> None:
        """Initialize the target service."""
        self._registry = TargetRegistry.get_registry_singleton()

    def _get_target_class(self, *, target_type: str) -> type:
        """
        Get the target class for a given type name.

        Looks up the class in the module-level target class registry.

        Args:
            target_type: The exact class name of the target (e.g., 'TextTarget').

        Returns:
            The target class.

        Raises:
            ValueError: If the target type is not found.
        """
        cls = _TARGET_CLASS_REGISTRY.get(target_type)
        if cls is None:
            raise ValueError(
                f"Target type '{target_type}' not found. Available types: {sorted(_TARGET_CLASS_REGISTRY.keys())}"
            )
        return cls

    def _build_instance_from_object(self, *, target_registry_name: str, target_obj: Any) -> TargetInstance:
        """
        Build a TargetInstance from a registry object.

        Returns:
            TargetInstance with metadata derived from the object.
        """
        return target_object_to_instance(target_registry_name, target_obj)

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
            for entry in self._registry.get_all_instances()
        ]
        page, has_more = self._paginate(items=items, cursor=cursor, limit=limit)
        next_cursor = page[-1].target_registry_name if has_more and page else None
        return TargetListResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor, prev_cursor=cursor),
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
        obj = self._registry.get_instance_by_name(target_registry_name)
        if obj is None:
            return None
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=obj)

    def get_target_object(self, *, target_registry_name: str) -> Any | None:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.get_instance_by_name(target_registry_name)

    async def create_target_async(self, *, request: CreateTargetRequest) -> TargetInstance:
        """
        Create a new target instance from API request.

        Instantiates the target with the given type and params,
        then registers it in the registry under its registry name.

        Args:
            request: The create target request with type, params, and auth_mode.

        Returns:
            TargetInstance with the new target's details.

        Raises:
            ValueError: if any of the following occur:
                - Target type in request is not found in the class registry;
                - Entra ID auth is requested but the target type does not support it;
                - Entra ID auth is requested for an OpenAI target or AzureMLChatTarget
                    but the endpoint is not valid (not managed by correct hosts);
                - If auth_mode='api_key' is set for a target but no key is supplied;
                - For RoundRobinTarget: if target_registry_names are missing, any name
                    is not found, or inner targets fail compatibility checks.
        """
        target_class = self._get_target_class(target_type=request.type)

        # RoundRobinTarget needs special handling: the user passes registry names
        # of existing targets, and we resolve them to live objects.
        if request.type == "RoundRobinTarget":
            target_obj = self._create_round_robin_target(params=dict(request.params))
        else:
            # Copy params so we can modify values (eg api_key) without changing request.params.
            params: dict[str, Any] = dict(request.params)

            if request.auth_mode == "entra":
                params = self._apply_entra_auth(target_class=target_class, target_type=request.type, params=params)
            else:
                self._validate_api_key_auth(target_class=target_class, params=params)

            target_obj = target_class(**params)

        self._registry.register_instance(target_obj)

        target_registry_name = target_obj.get_identifier().unique_name
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target_obj)

    def _create_round_robin_target(self, *, params: dict[str, Any]) -> RoundRobinTarget:
        """
        Resolve registry names to target objects and create a RoundRobinTarget.

        Targets resolving to the same ``ComponentIdentifier.hash`` are deduplicated
        before construction (mirroring ``TargetInitializer._auto_group_targets``)
        so duplicate registry aliases for the same underlying endpoint do not
        produce a rotation that hits one target twice. If fewer than 2 distinct
        targets remain after dedup, a ``ValueError`` is raised.

        The RoundRobinTarget constructor validates all compatibility requirements
        (same class, same configuration, same behavioral params, ≥2 targets).

        Args:
            params: Must contain ``target_registry_names`` (list of registry name
                strings). May contain ``weights`` (list of positive ints) of the
                same length as ``target_registry_names``; weights for deduped
                entries are dropped along with their target.

        Returns:
            A new RoundRobinTarget wrapping the resolved (deduped) targets.

        Raises:
            ValueError: If fewer than 2 names are supplied, a name is not found
                in the registry, weights length does not match, dedup leaves
                fewer than 2 distinct targets, or the RoundRobinTarget
                constructor rejects the combination.
        """
        registry_names: list[str] = params.get("target_registry_names", [])
        if len(registry_names) < 2:
            raise ValueError("RoundRobinTarget requires at least 2 target_registry_names in params.")

        raw_weights: list[int] | None = params.get("weights") or None
        if raw_weights is not None and len(raw_weights) != len(registry_names):
            raise ValueError(
                f"weights length ({len(raw_weights)}) must match target_registry_names length ({len(registry_names)})."
            )

        # Deduplicate by ComponentIdentifier hash: two registry entries that
        # resolve to the same identifier (same endpoint, model, api_version, etc.)
        # would just hit the same target twice in the rotation. This mirrors the
        # dedup in TargetInitializer._auto_group_targets so user-driven and
        # auto-grouped flows behave the same.
        seen_hashes: set[str | None] = set()
        resolved_targets: list[PromptTarget] = []
        resolved_weights: list[int] = []
        duplicates: list[str] = []
        for idx, name in enumerate(registry_names):
            target_obj = self._registry.get_instance_by_name(name)
            if target_obj is None:
                raise ValueError(f"Target '{name}' not found in the registry.")
            target_hash = target_obj.get_identifier().hash
            if target_hash in seen_hashes:
                duplicates.append(name)
                logger.debug(f"Skipping duplicate target '{name}' (hash {target_hash}) in RoundRobinTarget creation")
                continue
            seen_hashes.add(target_hash)
            resolved_targets.append(target_obj)
            if raw_weights is not None:
                resolved_weights.append(raw_weights[idx])

        if len(resolved_targets) < 2:
            raise ValueError(
                f"RoundRobinTarget requires at least 2 distinct targets, but the provided names "
                f"resolved to {len(resolved_targets)} unique target(s) after deduplication. "
                f"Duplicate names skipped: {duplicates}. Please select targets with different "
                f"endpoints or configurations."
            )

        weights = resolved_weights if raw_weights is not None else None

        # The constructor validates same-class, same-config, behavioral consistency, etc.
        return RoundRobinTarget(targets=resolved_targets, weights=weights)

    @staticmethod
    def _apply_entra_auth(*, target_class: type, target_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Replace ``api_key`` in ``params`` with an Entra ID token provider for
        the given target class.

        Args:
            target_class (type): The target class being instantiated
            target_type (str): The user-facing target type name
            params (dict[str, Any]): The target constructor parameters from the request

        Returns:
            dict[str, Any]: A new params dict with ``api_key`` replaced by an async
            token-provider callable suitable for the target class.

        Raises:
            ValueError: If the target type does not support Entra ID, if an
                OpenAI target is given a non-Azure endpoint, or if an
                AzureMLChatTarget is given a non-AML endpoint.
        """
        new_params = dict(params)
        if "api_key" in new_params:
            logger.debug("Discarding 'api_key' from params because auth_mode='entra'.")
            new_params.pop("api_key", None)

        if issubclass(target_class, OpenAITarget):
            endpoint = new_params.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint:
                raise ValueError("Entra ID authentication requires an 'endpoint' in params.")
            if not _is_azure_openai_endpoint(endpoint):
                raise ValueError(
                    "Entra ID authentication requires an Azure endpoint "
                    f"(*.openai.azure.com or *.ai.azure.com). Got: {endpoint}"
                )
            new_params["api_key"] = get_azure_openai_auth(endpoint)
            return new_params

        if issubclass(target_class, AzureMLChatTarget):
            endpoint = new_params.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint:
                raise ValueError("Entra ID authentication requires an 'endpoint' in params.")
            if not _is_azure_ml_endpoint(endpoint):
                raise ValueError(
                    "Entra ID authentication for AzureMLChatTarget requires an AML endpoint "
                    f"(*.inference.ml.azure.com). Got: {endpoint}"
                )
            new_params["api_key"] = get_azure_async_token_provider(_AZURE_ML_SCOPE)
            return new_params

        raise ValueError(
            f"Target type '{target_type}' does not support Entra ID authentication. "
            "Supported types are OpenAI-family targets and AzureMLChatTarget."
        )

    @staticmethod
    def _validate_api_key_auth(*, target_class: type, params: dict[str, Any]) -> None:
        """
        Enforce that ``auth_mode='api_key'`` actually has a usable key.

        Targets that do not authenticate via an api_key (e.g. ``TextTarget``)
        are skipped since they have no env var and the underlying
        constructor does not take any ``api_key`` arguments.

        Args:
            target_class (type): The target class being instantiated.
            params (dict[str, Any]): The constructor parameters from the request.

        Raises:
            ValueError: If no API key is provided in params or in the relevant
                environment variable for a target class that authenticates via
                an API key.
        """
        env_var = _resolve_api_key_env_var(target_class)
        if env_var is None:
            return

        if params.get("api_key"):
            return
        if os.environ.get(env_var):
            return

        raise ValueError(
            f"auth_mode='api_key' requires an API key but none was provided. "
            f"Pass 'api_key' in params or set the {env_var} environment variable. "
            "To authenticate with Microsoft Entra ID instead, set auth_mode='entra'."
        )


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
