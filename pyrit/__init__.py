# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""PyRIT public package API."""

import os
import sys
from types import ModuleType
from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    import pyrit.common.turn_off_transformers_warning as turn_off_transformers_warning
    from pyrit._version import __version__
    from pyrit.show_versions import show_versions

# Most people install PyRIT without torch, so suppress the transformers advisory
# before any PyRIT submodule can import transformers.
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "True"

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "__version__": "pyrit._version",
    "show_versions": "pyrit.show_versions",
    "turn_off_transformers_warning": ("pyrit.common.turn_off_transformers_warning", None),
}

__all__ = list(_LAZY_EXPORTS)


class _LazyPyRITModule(ModuleType):
    """Resolve exports that share a name with an imported child module."""

    def __getattribute__(self, name: str) -> object:
        if name == "show_versions":
            module_globals = ModuleType.__getattribute__(self, "__dict__")
            return resolve_lazy_export(
                name=name,
                module_name=__name__,
                module_globals=module_globals,
                exports=_LAZY_EXPORTS,
            )
        return ModuleType.__getattribute__(self, name)


sys.modules[__name__].__class__ = _LazyPyRITModule


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
