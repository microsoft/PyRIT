# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer service for catalog, registration, and apply-now operations.
"""

import asyncio
import logging
from functools import lru_cache
from typing import Any

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.initializers import (
    ApplyInitializerResponse,
    CustomInitializerListResponse,
    CustomInitializerResponse,
    ListRegisteredInitializersResponse,
)
from pyrit.models.catalog.initializer import RegisteredInitializer
from pyrit.registry import InitializerMetadata, InitializerRegistry
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


def _metadata_to_registered_initializer(metadata: InitializerMetadata) -> RegisteredInitializer:
    """
    Convert initializer metadata into a response model.

    Args:
        metadata: The registry metadata for an initializer.

    Returns:
        RegisteredInitializer: The response model representation.
    """
    return RegisteredInitializer(
        initializer_name=metadata.registry_name,
        initializer_type=metadata.class_name,
        description=metadata.class_description,
        required_env_vars=list(metadata.required_env_vars),
        supported_parameters=list(metadata.supported_parameters),
    )


class InitializerService:
    """
    Service for listing, registering, and applying initializers.
    """

    def __init__(self) -> None:
        """Initialize the initializer service."""
        self._registry = InitializerRegistry.get_registry_singleton()

    async def list_initializers_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ListRegisteredInitializersResponse:
        """
        List all available initializers with pagination.

        Args:
            limit: Maximum items to return per page.
            cursor: Pagination cursor (initializer_name to start after).

        Returns:
            ListRegisteredInitializersResponse: Paginated initializer summaries.
        """
        all_metadata = self._registry.get_all_registered_class_metadata()
        all_summaries = [_metadata_to_registered_initializer(m) for m in all_metadata]

        page, has_more = self._paginate(items=all_summaries, cursor=cursor, limit=limit)
        next_cursor = page[-1].initializer_name if has_more and page else None

        return ListRegisteredInitializersResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor, prev_cursor=cursor),
        )

    async def get_initializer_async(self, *, initializer_name: str) -> RegisteredInitializer | None:
        """
        Get a single initializer by registry name.

        Args:
            initializer_name: The registry key of the initializer.

        Returns:
            RegisteredInitializer | None: The matching initializer, if found.
        """
        metadata = self._get_metadata_by_name().get(initializer_name)
        return _metadata_to_registered_initializer(metadata) if metadata else None

    async def apply_initializer_async(
        self,
        *,
        initializer_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> ApplyInitializerResponse:
        """
        Build, validate, and run one initializer immediately.

        The build/validate/initialize steps run in a worker thread because an initializer's
        ``initialize_async`` can perform blocking I/O (e.g. target construction acquiring Entra
        tokens). Running it inline would block the event loop and make the backend unresponsive
        to concurrent requests for the duration of the apply.

        Args:
            initializer_name: The initializer registry name to execute.
            parameters: Optional one-time parameters for this execution.

        Returns:
            ApplyInitializerResponse: Success metadata for the apply-now execution.
        """
        await asyncio.to_thread(
            self._build_and_run_initializer,
            initializer_name=initializer_name,
            parameters=parameters,
        )

        return ApplyInitializerResponse(
            initializer_name=initializer_name,
            status="applied",
            applied_parameters=parameters,
        )

    async def register_initializer_async(
        self,
        *,
        name: str,
        script_content: str,
    ) -> RegisteredInitializer:
        """
        Register an initializer from uploaded Python source code.

        Args:
            name: Registry name for the new initializer.
            script_content: Python source code containing a PyRITInitializer subclass.

        Returns:
            RegisteredInitializer: The newly registered initializer summary.
        """
        await asyncio.to_thread(self._registry.register_from_content, name=name, script_content=script_content)

        initializer = await self.get_initializer_async(initializer_name=name)
        if not initializer:
            raise ValueError(f"Initializer '{name}' was registered but metadata could not be retrieved.")
        return initializer

    async def list_custom_initializers_async(self) -> CustomInitializerListResponse:
        """
        List custom initializer scripts from the registry's configured storage.

        Returns:
            CustomInitializerListResponse: The configured source and stored scripts.
        """
        source, scripts = await asyncio.to_thread(self._registry.list_stored_initializer_sources)
        return CustomInitializerListResponse(
            source=source,
            items=[
                CustomInitializerResponse(
                    initializer_name=name,
                    script_content=script_content,
                    source=script_source,
                )
                for name, script_content, script_source in scripts
            ],
        )

    async def unregister_initializer_async(self, *, initializer_name: str) -> None:
        """
        Remove a custom initializer from the registry.

        Args:
            initializer_name: The registry name to remove.
        """
        await asyncio.to_thread(self._registry.unregister_and_cleanup, initializer_name)
        logger.info("Unregistered initializer: %s", initializer_name)

    def _build_and_run_initializer(
        self,
        *,
        initializer_name: str,
        parameters: dict[str, Any] | None,
    ) -> None:
        """
        Build, validate, and run one initializer synchronously (for thread offload).

        Args:
            initializer_name: The initializer registry name to execute.
            parameters: Optional parameters for this execution.
        """
        initializer = self._registry.create_and_configure(
            initializer_name,
            initializer_params=parameters or None,
        )
        self._validate_parameter_values(instance=initializer, parameters=parameters)
        initializer.validate()
        asyncio.run(initializer.initialize_async())

    @staticmethod
    def _validate_parameter_values(*, instance: PyRITInitializer, parameters: dict[str, Any] | None) -> None:
        """
        Validate raw parameter values against each declared parameter's type.

        ``create_and_configure`` only checks parameter *names*; this coerces each provided
        value with ``Parameter.coerce_value`` so a value that cannot satisfy its declared
        type (e.g. a non-integer ``days`` or an out-of-set tag) is rejected up front with a
        clear error instead of failing later when the initializer runs.

        Args:
            instance: The configured initializer whose ``supported_parameters`` declare the types.
            parameters: The raw parameter values to validate.

        Raises:
            ValueError: If a value cannot be coerced to its parameter's declared type.
        """
        if not parameters:
            return
        parameter_by_name = {parameter.name: parameter for parameter in instance.supported_parameters}
        for name, value in parameters.items():
            parameter = parameter_by_name.get(name)
            if parameter is not None:
                parameter.coerce_value(value)

    def _get_metadata_by_name(self) -> dict[str, InitializerMetadata]:
        return {metadata.registry_name: metadata for metadata in self._registry.get_all_registered_class_metadata()}

    @staticmethod
    def _paginate(
        *,
        items: list[RegisteredInitializer],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[RegisteredInitializer], bool]:
        """
        Apply cursor-based pagination.

        Args:
            items: Full list of items.
            cursor: Initializer name to start after.
            limit: Maximum items per page.

        Returns:
            tuple[list[RegisteredInitializer], bool]: Paginated items and has-more flag.
        """
        start_idx = 0
        if cursor:
            for index, item in enumerate(items):
                if item.initializer_name == cursor:
                    start_idx = index + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more


@lru_cache(maxsize=1)
def get_initializer_service() -> InitializerService:
    """
    Get the global initializer service instance.

    Returns:
        InitializerService: The singleton initializer service instance.
    """
    return InitializerService()
