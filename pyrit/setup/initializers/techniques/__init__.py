# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Scenario attack technique groups and the TechniqueInitializer."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.setup.initializers.techniques.technique_initializer import (
        TechniqueInitializer,
        TechniqueInitializerTags,
        build_technique_factories,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "TechniqueInitializer": "pyrit.setup.initializers.techniques.technique_initializer",
    "TechniqueInitializerTags": "pyrit.setup.initializers.techniques.technique_initializer",
    "build_technique_factories": "pyrit.setup.initializers.techniques.technique_initializer",
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
