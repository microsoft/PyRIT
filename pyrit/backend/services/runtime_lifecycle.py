# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-process admission and fail-fast runtime replacement."""

import asyncio
import logging
import os
import uuid
from typing import Any

from fastapi import FastAPI

from pyrit.backend.models.initializers import ConfiguredInitializerSetting
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.environment_file_service import EnvironmentFileService
from pyrit.backend.services.scenario_run_service import get_scenario_run_service, peek_scenario_run_service
from pyrit.backend.services.service_lifecycle import close_services_async, outstanding_estimates
from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH
from pyrit.registry import InitializerRegistry
from pyrit.setup.configuration_loader import ConfigurationLoader
from pyrit.setup.environment_loading import resolve_environment_async
from pyrit.setup.initialization import validate_reinitialization_memory

logger = logging.getLogger(__name__)


class RuntimeLifecycle:
    """Own runtime readiness, admission, preflight, and idle replacement."""

    def __init__(self, *, app: FastAPI, source: ConfigurationFileService) -> None:
        """Bind lifecycle state to one app and its immutable configuration source."""
        self.app = app
        self.app.state.auth_environment = {
            key: os.getenv(key, "")
            for key in (
                "ENTRA_CLIENT_ID",
                "ENTRA_TENANT_ID",
                "ENTRA_ALLOWED_GROUP_IDS",
                "PYRIT_ALLOW_UNAUTHENTICATED_ADMIN",
            )
        }
        self.source = source
        self.edit_lock = asyncio.Lock()
        self.state = "initializing"
        self.generation = ""
        self.version: str | None = None
        self.outcome = "starting"
        self.message = ""
        self.operations: set[asyncio.Task[None]] = set()
        self.management_operations: set[asyncio.Task[None]] = set()
        self.apply_task: asyncio.Task[None] | None = None
        self.topology_supported = all(
            os.getenv(key, "1") == "1"
            for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "PYRIT_API_WORKERS", "PYRIT_REPLICAS")
        )

    def status(self) -> dict[str, Any]:
        """Return status recoverable after a disconnected apply."""
        return {
            "state": self.state,
            "generation": self.generation,
            "version": self.version,
            "outcome": self.outcome,
            "message": self.message,
            "enabled": self.topology_supported,
            "applying": self.apply_task is not None and not self.apply_task.done(),
        }

    async def _load_async(self) -> ConfigurationLoader:
        async with self.source.resolve_async() as file:
            return await asyncio.to_thread(ConfigurationLoader.load_with_overrides, config_file=file, strict=True)

    async def _management_async(self, config: ConfigurationLoader) -> None:
        resolved = config.resolve_env_files()
        read_only = {}
        if os.getenv("PYRIT_ENV_CONTENTS"):
            read_only[CONFIGURATION_DIRECTORY_PATH / ".env"] = (
                "Materialized from the deployment secret; update that secret instead."
            )
        self.app.state.environment_file_service = EnvironmentFileService(
            resolved_env_files=list(resolved) if resolved is not None else None,
            env_akv_ref=config.env_akv_ref,
            env_akv_strict=config.env_akv_strict,
            read_only_file_sources=read_only,
        )
        self.app.state.allow_custom_initializers = config.allow_custom_initializers
        registry = await asyncio.to_thread(InitializerRegistry.get_registry_singleton)
        registry.configure_custom_scripts_source(config.custom_initializers_source)

    def _publish(self, config: ConfigurationLoader) -> None:
        self.app.state.configured_initializers = [
            ConfiguredInitializerSetting(initializer_name=item.name, parameters=item.args, order_index=index)
            for index, item in enumerate(config.initializer_configs)
        ]
        self.app.state.default_labels = {
            key: value for key, value in (("operator", config.operator), ("operation", config.operation)) if value
        }
        self.app.state.max_concurrent_scenario_runs = config.max_concurrent_scenario_runs
        self.generation = str(uuid.uuid4())
        self.state, self.outcome, self.message = "ready", "success", "PyRIT is ready."

    async def startup_async(self) -> None:
        """Keep management alive even when parsing or initialization fails."""
        self.app.state.allow_custom_initializers = False
        self.app.state.environment_file_service = None
        self.app.state.configured_initializers = []
        self.app.state.default_labels = {}
        try:
            config = await self._load_async()
            await self._management_async(config)
            registry = InitializerRegistry.get_registry_singleton()
            if config.allow_custom_initializers:
                logger.warning("Custom initializer registration is ENABLED (allow_custom_initializers: true).")
                await asyncio.to_thread(registry.register_stored_initializers, strict=True)
            await config.initialize_pyrit_async(raise_on_initializer_error=True)
            await get_scenario_run_service().reconcile_interrupted_runs_async()
            _, self.version = await self.source.read_with_version_async()
            self._publish(config)
        except Exception:
            self.state, self.outcome = "restart-required", "initialization-failed"
            self.message = "PyRIT setup failed. Repair saved configuration, then restart the backend."
            logger.exception("PyRIT startup failed; configuration recovery remains available.")

    def begin_apply(self, *, version: str) -> dict[str, Any]:
        """
        Start a retained operation independent of a client connection.

        Returns:
            dict[str, Any]: Accepted operation or admission rejection.
        """
        if not self.topology_supported:
            return {
                **self.status(),
                "outcome": "unsupported",
                "message": ("Reinitialization requires one backend worker and one replica."),
            }
        if self.state in ("failed", "restart-required"):
            return {**self.status(), "outcome": "restart-required"}
        if (self.apply_task and not self.apply_task.done()) or self.edit_lock.locked() or self.management_operations:
            return {**self.status(), "outcome": "busy"}
        self.apply_task = asyncio.create_task(self._apply_async(version=version))
        self.outcome, self.message = "validating", "Validating saved sources."
        return {**self.status(), "outcome": "accepted"}

    async def _apply_async(self, *, version: str) -> None:
        async with self.edit_lock:
            mutated = False
            previous_state = self.state
            try:
                _, current_version = await self.source.read_with_version_async()
                if current_version != version:
                    self.outcome, self.message = "version-conflict", "Configuration changed; reload the saved file."
                    return
                config = await self._load_async()
                if not config.enable_live_reinitialization:
                    self.outcome = "unsupported"
                    self.message = (
                        "Set enable_live_reinitialization: true in the saved configuration to use live apply."
                    )
                    return
                # Type compatibility is checked before contacting environment sources.
                validate_reinitialization_memory(
                    memory_db_type=config._MEMORY_DB_TYPE_MAP[config.memory_db_type], environment={}
                )
                values = await resolve_environment_async(
                    env_files=config.resolve_env_files(),
                    env_akv_ref=config.env_akv_ref,
                    env_akv_strict=config.env_akv_strict,
                    silent=True,
                )
                validate_reinitialization_memory(
                    memory_db_type=config._MEMORY_DB_TYPE_MAP[config.memory_db_type], environment=values
                )
                prepared = await config.preflight_reinitialization_async(environment_values=values)
                _, checked_version = await self.source.read_with_version_async()
                if checked_version != current_version:
                    self.outcome, self.message = "version-conflict", "Configuration changed during validation."
                    return
                if self._has_active_work():
                    self.outcome = "busy"
                    self.message = "Wait for active work to finish or cancel it with its existing controls, then retry."
                    return

                # Changing state closes runtime admission. Recheck after the barrier so
                # work admitted immediately before it cannot overlap replacement.
                self.state, self.outcome, self.message = "initializing", "initializing", "Applying saved sources."
                if self._has_active_work():
                    self.state = previous_state
                    self.outcome = "busy"
                    self.message = "New work was admitted before apply started. Wait for it to finish, then retry."
                    return

                mutated = True
                await close_services_async()
                await config.apply_prepared_reinitialization_async(prepared=prepared)
                await self._management_async(config)
                self.version = current_version
                self._publish(config)
            except Exception:
                logger.exception(
                    "PyRIT live apply failed phase=%s generation=%s",
                    "mutation" if mutated else "preflight",
                    self.generation,
                )
                self.outcome = "restart-required" if mutated else "invalid-configuration"
                if mutated:
                    self.state = "restart-required"
                else:
                    self.state = previous_state
                self.message = (
                    "Live initialization failed after replacement began. Restart the backend."
                    if mutated
                    else "Configuration or memory settings are invalid. Memory changes require a restart."
                )
            finally:
                logger.info("PyRIT apply outcome=%s generation=%s", self.outcome, self.generation)

    def _has_active_work(self) -> bool:
        """Return whether any admitted or background runtime operation remains."""
        service = peek_scenario_run_service()
        return bool((service and service.has_active_work()) or self.operations or outstanding_estimates())

    async def shutdown_async(self) -> None:
        """Stop the current scheduler and close services owned by this process."""
        if self.apply_task and not self.apply_task.done():
            await asyncio.shield(self.apply_task)
        self.state = "stopping"
        service = peek_scenario_run_service()
        if service:
            await service.shutdown_async()
        await close_services_async()
