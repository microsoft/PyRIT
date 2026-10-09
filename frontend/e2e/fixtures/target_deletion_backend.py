# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Isolated real registry for target-deletion browser tests."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI

from frontend.e2e.fixtures.manual_send_backend import _offline_lifespan_async, app
from pyrit.setup.initializers import TargetInitializer


@asynccontextmanager
async def _deletion_lifespan_async(application: FastAPI) -> AsyncIterator[None]:
    protected = os.environ.get("PYRIT_E2E_PROTECTED_TARGETS") == "true"
    async with _offline_lifespan_async(application):
        if protected:
            env = {}
            for prefix, host in (
                ("OPENAI_CHAT", "first"),
                ("PLATFORM_OPENAI_CHAT", "second"),
                ("ADVERSARIAL_CHAT", "adversary"),
            ):
                env.update(
                    {
                        f"{prefix}_ENDPOINT": f"https://{host}.example/v1",
                        f"{prefix}_KEY": "offline-placeholder",
                        f"{prefix}_MODEL": "gpt-4o",
                    }
                )
            with patch.dict(os.environ, env):
                await TargetInitializer().initialize_async()
        yield


app.router.lifespan_context = _deletion_lifespan_async
