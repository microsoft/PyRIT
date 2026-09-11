# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Common utilities and helpers for PyRIT.

Heavy submodules (download_hf_model, net_utility) are intentionally NOT
re-exported here to keep ``import pyrit`` fast.  Import them directly, e.g.::

    from pyrit.common.net_utility import get_httpx_client

``Parameter`` is not part of ``pyrit.common``; it lives in ``pyrit.models``.
"""

import sys
from types import ModuleType
from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.common.apply_defaults import (
        REQUIRED_VALUE,
        DefaultValueScope,
        apply_defaults,
        apply_defaults_to_method,
        get_global_default_values,
        reset_default_values,
        set_default_value,
    )
    from pyrit.common.brick_contract import enforce_keyword_only_init, forward_init_parameters
    from pyrit.common.default_values import get_non_required_value, get_required_value
    from pyrit.common.deprecation import print_deprecation_message
    from pyrit.common.mime_type import get_mime_type
    from pyrit.common.notebook_utils import is_in_ipython_session
    from pyrit.common.singleton import Singleton
    from pyrit.common.utils import (
        combine_dict,
        combine_list,
        get_kwarg_param,
        get_random_indices,
        verify_and_resolve_path,
        warn_if_set,
    )
    from pyrit.common.yaml_loadable import YamlLoadable

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "apply_defaults": "pyrit.common.apply_defaults",
    "apply_defaults_to_method": "pyrit.common.apply_defaults",
    "combine_dict": "pyrit.common.utils",
    "combine_list": "pyrit.common.utils",
    "DefaultValueScope": "pyrit.common.apply_defaults",
    "enforce_keyword_only_init": "pyrit.common.brick_contract",
    "forward_init_parameters": "pyrit.common.brick_contract",
    "get_global_default_values": "pyrit.common.apply_defaults",
    "get_kwarg_param": "pyrit.common.utils",
    "get_mime_type": "pyrit.common.mime_type",
    "get_non_required_value": "pyrit.common.default_values",
    "get_random_indices": "pyrit.common.utils",
    "get_required_value": "pyrit.common.default_values",
    "is_in_ipython_session": "pyrit.common.notebook_utils",
    "print_deprecation_message": "pyrit.common.deprecation",
    "REQUIRED_VALUE": "pyrit.common.apply_defaults",
    "reset_default_values": "pyrit.common.apply_defaults",
    "set_default_value": "pyrit.common.apply_defaults",
    "Singleton": "pyrit.common.singleton",
    "verify_and_resolve_path": "pyrit.common.utils",
    "warn_if_set": "pyrit.common.utils",
    "YamlLoadable": "pyrit.common.yaml_loadable",
}

__all__ = list(_LAZY_EXPORTS)


class _LazyCommonModule(ModuleType):
    """Resolve exports that share a name with an imported child module."""

    def __getattribute__(self, name: str) -> object:
        if name == "apply_defaults":
            module_globals = ModuleType.__getattribute__(self, "__dict__")
            return resolve_lazy_export(
                name=name,
                module_name=__name__,
                module_globals=module_globals,
                exports=_LAZY_EXPORTS,
            )
        return ModuleType.__getattribute__(self, name)


sys.modules[__name__].__class__ = _LazyCommonModule


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
