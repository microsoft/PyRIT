# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ASGI-level admission that retains server work after a client disconnect."""

import asyncio
from typing import TYPE_CHECKING

from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle


class RuntimeAdmissionMiddleware:
    """Guard all runtime routes and serialize management writes against apply."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Admit and retain requests until server-side work finishes."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        runtime: RuntimeLifecycle | None = getattr(scope["app"].state, "runtime_lifecycle", None)
        path = get_route_path(scope)
        if (
            runtime is None
            or not path.startswith("/api/")
            or path
            in (
                "/api/health",
                "/api/runtime",
                "/api/version",
                "/api/auth/config",
                "/api/auth/access",
            )
            or path.startswith("/api/auth/")
        ):
            await self.app(scope, receive, send)
            return
        management = path == "/api/config" or path.startswith(("/api/config/", "/api/initializers"))
        apply_route = path.startswith("/api/config/runtime")
        write = management and scope["method"] not in ("GET", "HEAD", "OPTIONS") and not apply_route
        if runtime.target_cleanup_active and not runtime.is_stopping:
            # A deletion closes one target's clients; the runtime itself stays ready.
            await JSONResponse(
                {"detail": "A target deletion is in progress; retry shortly."},
                status_code=503,
                headers={"Retry-After": "1"},
            )(scope, receive, send)
            return
        if (
            runtime.is_stopping
            or (write and (runtime.edit_lock.locked() or (runtime.apply_task and not runtime.apply_task.done())))
            or (path.startswith("/api/initializers") and runtime.edit_lock.locked())
            or (path.startswith("/api/initializers") and runtime.state == "initializing")
            or (not management and runtime.state != "ready")
        ):
            await JSONResponse(
                {"detail": "PyRIT runtime is unavailable; inspect runtime status or repair configuration."},
                status_code=503,
            )(scope, receive, send)
            return
        if apply_route:
            await self.app(scope, receive, send)
            return

        target_delete = scope["method"] == "DELETE" and path.startswith("/api/targets/") and path.count("/") == 3
        if target_delete and not runtime.begin_target_cleanup():
            await JSONResponse(
                {"detail": "Targets cannot be deleted while runtime work is active. Wait for it to finish and retry."},
                status_code=409,
            )(scope, receive, send)
            return

        async def execute_async() -> None:
            try:
                if write:
                    async with runtime.edit_lock:
                        await self.app(scope, receive, send)
                else:
                    await self.app(scope, receive, send)
            finally:
                runtime.operations.discard(task)
                runtime.management_operations.discard(task)
                if target_delete:
                    runtime.target_cleanup_active = False

        task = asyncio.create_task(execute_async())
        task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        if management:
            runtime.management_operations.add(task)
        else:
            runtime.operations.add(task)
        # Strong ownership plus shield ensures sends/threads finish even when an HTTP task is cancelled.
        await asyncio.shield(task)
