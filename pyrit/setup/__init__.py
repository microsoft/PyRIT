# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Module containing initialization PyRIT."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.setup.configuration_loader import ConfigurationLoader, initialize_from_config_async
    from pyrit.setup.initialization import AZURE_SQL, IN_MEMORY, SQLITE, MemoryDatabaseType, initialize_pyrit_async

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AZURE_SQL": "pyrit.setup.initialization",
    "SQLITE": "pyrit.setup.initialization",
    "IN_MEMORY": "pyrit.setup.initialization",
    "initialize_pyrit_async": "pyrit.setup.initialization",
    "initialize_from_config_async": "pyrit.setup.configuration_loader",
    "MemoryDatabaseType": "pyrit.setup.initialization",
    "ConfigurationLoader": "pyrit.setup.configuration_loader",
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
