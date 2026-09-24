# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ASGI-level admission that retains server work after a client disconnect."""

import asyncio
from typing import TYPE_CHECKING

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
        path = scope["path"]
        if (
            runtime is None
            or not path.startswith("/api/")
            or path
            in (
                "/api/health",
                "/api/runtime",
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
        cancel = path.startswith("/api/scenarios/runs/") and path.endswith("/cancel")
        if (
            (write and (runtime.edit_lock.locked() or (runtime.apply_task and not runtime.apply_task.done())))
            or (path.startswith("/api/initializers") and runtime.edit_lock.locked())
            or (path.startswith("/api/initializers") and runtime.state == "initializing")
            or (
                not management
                and runtime.state != "ready"
                and not (cancel and runtime.state in ("stopping", "blocked"))
            )
        ):
            await JSONResponse(
                {"detail": "PyRIT runtime is unavailable; inspect runtime status or repair configuration."},
                status_code=503,
            )(scope, receive, send)
            return
        if apply_route:
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["runtime_admission_epoch"] = runtime.admission_epoch

        async def execute_async() -> None:
            try:
                if write:
                    async with runtime.edit_lock:
                        await self.app(scope, receive, send)
                else:
                    await self.app(scope, receive, send)
            finally:
                if task in runtime.operations:
                    del runtime.operations[task]
                runtime.management_operations.discard(task)

        task = asyncio.create_task(execute_async())
        task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        if management:
            runtime.management_operations.add(task)
        else:
            runtime.operations[task] = f"{scope['method']} {path}"
            if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
                runtime.work_revision += 1
        # Strong ownership plus shield ensures sends/threads finish even when an HTTP task is cancelled.
        await asyncio.shield(task)
