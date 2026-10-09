# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline backend for manual-send browser tests, with no real configuration or providers."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI

from pyrit.backend.main import app, lifespan
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.configuration_loader import ConfigurationLoader


async def _initialize_offline_async(self: ConfigurationLoader, *, raise_on_initializer_error: bool = True) -> None:
    await initialize_pyrit_async(
        memory_db_type="InMemory",
        load_defaults=False,
        env_files=[],
        env_akv_ref=[],
        raise_on_initializer_error=raise_on_initializer_error,
    )


@asynccontextmanager
async def _offline_lifespan_async(application: FastAPI) -> AsyncGenerator[None, None]:
    with TemporaryDirectory(prefix="pyrit-e2e-instance-recipes-") as instance_recipes:
        config = ConfigurationLoader(
            memory_db_type="in_memory", env_files=[], env_akv_ref=[], instance_recipes_source=instance_recipes
        )
        # Requests never carry keys, so the loopback OpenAI targets read this placeholder.
        environment = {"PYRIT_DEV_MODE": "true", "OPENAI_CHAT_KEY": "local-recovery-test-placeholder"}
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                ConfigurationFileService,
                "_read_source_async",
                new=AsyncMock(return_value="memory_db_type: in_memory\nenv_files: []\ninitializers: []\n"),
            ),
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=_initialize_offline_async),
        ):
            async with lifespan(application):
                yield


app.router.lifespan_context = _offline_lifespan_async
