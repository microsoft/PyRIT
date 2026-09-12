# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
API route handlers.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.backend.routes import (
        attacks,
        converters,
        datasets,
        health,
        initializers,
        labels,
        media,
        scenarios,
        targets,
        version,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "attacks": ("pyrit.backend.routes.attacks", None),
    "converters": ("pyrit.backend.routes.converters", None),
    "datasets": ("pyrit.backend.routes.datasets", None),
    "health": ("pyrit.backend.routes.health", None),
    "initializers": ("pyrit.backend.routes.initializers", None),
    "labels": ("pyrit.backend.routes.labels", None),
    "media": ("pyrit.backend.routes.media", None),
    "scenarios": ("pyrit.backend.routes.scenarios", None),
    "targets": ("pyrit.backend.routes.targets", None),
    "version": ("pyrit.backend.routes.version", None),
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
