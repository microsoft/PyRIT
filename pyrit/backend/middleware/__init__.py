# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Middleware module for backend."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.backend.middleware.error_handlers import register_error_handlers
    from pyrit.backend.middleware.request_id import RequestIdMiddleware
    from pyrit.backend.middleware.security_headers import SecurityHeadersMiddleware

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "register_error_handlers": "pyrit.backend.middleware.error_handlers",
    "RequestIdMiddleware": "pyrit.backend.middleware.request_id",
    "SecurityHeadersMiddleware": "pyrit.backend.middleware.security_headers",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
