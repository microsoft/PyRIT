# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer service for catalog, settings, and apply-now operations.

Provides access to the ``InitializerRegistry`` plus persisted initializer
override rows stored in Central Memory.
"""

import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.initializers import (
    ApplyInitializerResponse,
    EffectiveInitializerSetting,
    ListEffectiveInitializerSettingsResponse,
    ListRegisteredInitializersResponse,
)
from pyrit.memory import CentralMemory
from pyrit.models import InitializerSetting
from pyrit.models.catalog.initializer import RegisteredInitializer
from pyrit.registry import InitializerMetadata, InitializerRegistry
from pyrit.setup.configuration_loader import InitializerConfig

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


def _missing_registered_initializer(initializer_name: str) -> RegisteredInitializer:
    """
    Build placeholder metadata for a saved override whose class is no longer registered.

    Args:
        initializer_name: The missing initializer's registry name.

    Returns:
        RegisteredInitializer: Placeholder metadata for display.
    """
    return RegisteredInitializer(
        initializer_name=initializer_name,
        initializer_type="UnknownInitializer",
        description="Initializer is no longer registered.",
        required_env_vars=[],
        supported_parameters=[],
    )


class InitializerService:
    """
    Service for listing, registering, configuring, and applying initializers.

    Uses ``InitializerRegistry`` for metadata/building and Central Memory for
    persisted override rows.
    """

    def __init__(self) -> None:
        """Initialize the initializer service."""
        self._registry = InitializerRegistry.get_registry_singleton()
        self._memory = CentralMemory.get_memory_instance()

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

    async def list_effective_initializer_settings_async(
        self,
        *,
        baseline_initializers: Sequence[InitializerConfig],
    ) -> ListEffectiveInitializerSettingsResponse:
        """
        Merge baseline initializer config with saved override rows.

        Args:
            baseline_initializers: The initializer list resolved from the config baseline.

        Returns:
            ListEffectiveInitializerSettingsResponse: The merged, ordered effective list.
        """
        metadata_by_name = self._get_metadata_by_name()
        saved_overrides = {setting.initializer_name: setting for setting in self._memory.get_initializer_settings()}
        ordered_items: list[tuple[int, int, int, EffectiveInitializerSetting]] = []
        seen_names: set[str] = set()

        for baseline_position, config in enumerate(baseline_initializers):
            override = saved_overrides.get(config.name)
            effective_parameters = override.parameters if override and override.parameters is not None else config.args
            source = "baseline+override" if override else "baseline"
            effective_order = (
                override.order_index if override and override.order_index is not None else baseline_position
            )
            registered_initializer = self._get_registered_initializer_for_name(
                initializer_name=config.name,
                metadata_by_name=metadata_by_name,
            )

            ordered_items.append(
                self._build_effective_item_sort_entry(
                    registered_initializer=registered_initializer,
                    enabled=override.enabled if override else True,
                    parameters=effective_parameters,
                    order_index=effective_order,
                    saved_order_index=override.order_index if override else None,
                    source=source,
                    insertion_order=baseline_position,
                )
            )
            seen_names.add(config.name)

        append_index = 0
        for initializer_name, override in saved_overrides.items():
            if initializer_name in seen_names:
                continue

            effective_order = (
                override.order_index if override.order_index is not None else len(baseline_initializers) + append_index
            )
            registered_initializer = self._get_registered_initializer_for_name(
                initializer_name=initializer_name,
                metadata_by_name=metadata_by_name,
            )

            ordered_items.append(
                self._build_effective_item_sort_entry(
                    registered_initializer=registered_initializer,
                    enabled=override.enabled,
                    parameters=override.parameters,
                    order_index=effective_order,
                    saved_order_index=override.order_index,
                    source="override",
                    insertion_order=len(baseline_initializers) + append_index,
                )
            )
            append_index += 1

        ordered_items.sort(key=lambda item: (item[0], item[1], item[2], item[3].initializer_name))
        return ListEffectiveInitializerSettingsResponse(items=[item[3] for item in ordered_items])

    async def save_initializer_setting_async(
        self,
        *,
        initializer_name: str,
        enabled: bool,
        parameters: dict[str, Any] | None,
        order_index: int | None,
    ) -> InitializerSetting:
        """
        Validate and persist a single initializer override row.

        Args:
            initializer_name: The initializer registry name.
            enabled: Whether the initializer should remain enabled.
            parameters: Optional parameter overrides to persist.
            order_index: Optional zero-based order override.

        Returns:
            InitializerSetting: The saved override row.
        """
        self._validate_initializer_parameters(initializer_name=initializer_name, parameters=parameters)
        setting = InitializerSetting(
            initializer_name=initializer_name,
            enabled=enabled,
            parameters=parameters,
            order_index=order_index,
        )
        self._memory.add_initializer_setting(setting=setting)
        return setting

    async def delete_initializer_setting_async(self, *, initializer_name: str) -> None:
        """
        Delete one saved initializer override row.

        Args:
            initializer_name: The initializer registry name to clear.
        """
        self._memory.delete_initializer_setting(initializer_name=initializer_name)

    async def apply_initializer_async(
        self,
        *,
        initializer_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> ApplyInitializerResponse:
        """
        Build, validate, and run one initializer immediately.

        Args:
            initializer_name: The initializer registry name to execute.
            parameters: Optional one-time parameters. When omitted, any saved override
                parameters are used instead.

        Returns:
            ApplyInitializerResponse: Success metadata for the apply-now execution.
        """
        resolved_parameters = parameters if parameters is not None else self._get_saved_parameters(initializer_name)
        initializer = self._registry.create_and_configure(
            initializer_name,
            initializer_params=resolved_parameters or None,
        )
        initializer.validate()
        await initializer.initialize_async()

        return ApplyInitializerResponse(
            initializer_name=initializer_name,
            status="applied",
            applied_parameters=resolved_parameters,
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
        self._registry.register_from_content(name=name, script_content=script_content)

        initializer = await self.get_initializer_async(initializer_name=name)
        if not initializer:
            raise ValueError(f"Initializer '{name}' was registered but metadata could not be retrieved.")
        return initializer

    async def unregister_initializer_async(self, *, initializer_name: str) -> None:
        """
        Remove a custom initializer from the registry.

        Args:
            initializer_name: The registry name to remove.
        """
        self._registry.unregister_and_cleanup(initializer_name)
        logger.info("Unregistered initializer: %s", initializer_name)

    def _validate_initializer_parameters(
        self,
        *,
        initializer_name: str,
        parameters: dict[str, Any] | None,
    ) -> None:
        """
        Ensure the initializer exists and its parameters are valid.

        Args:
            initializer_name: The initializer registry name.
            parameters: Optional initializer parameters to validate.
        """
        self._registry.create_and_configure(initializer_name, initializer_params=parameters or None)

    def _get_saved_parameters(self, initializer_name: str) -> dict[str, Any] | None:
        """
        Look up saved parameters for one initializer.

        Args:
            initializer_name: The initializer registry name.

        Returns:
            dict[str, Any] | None: Saved parameters, if present.
        """
        for setting in self._memory.get_initializer_settings():
            if setting.initializer_name == initializer_name:
                return setting.parameters
        return None

    def _get_metadata_by_name(self) -> dict[str, InitializerMetadata]:
        return {metadata.registry_name: metadata for metadata in self._registry.get_all_registered_class_metadata()}

    def _get_registered_initializer_for_name(
        self,
        *,
        initializer_name: str,
        metadata_by_name: dict[str, InitializerMetadata],
    ) -> RegisteredInitializer:
        metadata = metadata_by_name.get(initializer_name)
        if metadata:
            return _metadata_to_registered_initializer(metadata)
        return _missing_registered_initializer(initializer_name)

    @staticmethod
    def _build_effective_item_sort_entry(
        *,
        registered_initializer: RegisteredInitializer,
        enabled: bool,
        parameters: dict[str, Any] | None,
        order_index: int,
        saved_order_index: int | None,
        source: str,
        insertion_order: int,
    ) -> tuple[int, int, int, EffectiveInitializerSetting]:
        explicit_priority = 0 if saved_order_index is not None else 1
        return (
            order_index,
            explicit_priority,
            insertion_order,
            EffectiveInitializerSetting(
                **registered_initializer.model_dump(),
                enabled=enabled,
                parameters=parameters,
                order_index=order_index,
                saved_order_index=saved_order_index,
                source=source,
            ),
        )

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
