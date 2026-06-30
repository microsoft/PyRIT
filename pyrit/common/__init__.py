# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Common utilities and helpers for PyRIT.

Heavy submodules (display_response, download_hf_model, net_utility) are
intentionally NOT re-exported here to keep ``import pyrit`` fast.  Import them
directly, e.g.::

    from pyrit.common.net_utility import get_httpx_client

``Parameter`` is no longer part of ``pyrit.common``; it lives in ``pyrit.models``.
Accessing ``pyrit.common.Parameter`` (or ``from pyrit.common import Parameter``)
still resolves for one release but emits a ``DeprecationWarning``. Import from
``pyrit.models`` instead. This alias will be removed in 0.16.0.
"""

from pyrit.common.apply_defaults import (
    REQUIRED_VALUE,
    DefaultValueScope,
    apply_defaults,
    apply_defaults_to_method,
    get_global_default_values,
    reset_default_values,
    set_default_value,
)
from pyrit.common.brick_contract import enforce_keyword_only_init
from pyrit.common.default_values import get_non_required_value, get_required_value
from pyrit.common.deprecation import module_deprecation_getattr, print_deprecation_message
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

# ``Parameter`` moved to ``pyrit.models``. Resolve it lazily so that (a) ``pyrit.common``
# stays free of the heavy ``pyrit.models`` import on the fast CLI path, and (b) the
# deprecated ``from pyrit.common import Parameter`` access emits a one-time warning.
__getattr__ = module_deprecation_getattr(
    old_module="pyrit.common",
    target_module="pyrit.models",
    names=["Parameter"],
    removed_in="0.16.0",
)

__all__ = [
    "apply_defaults",
    "apply_defaults_to_method",
    "combine_dict",
    "combine_list",
    "DefaultValueScope",
    "enforce_keyword_only_init",
    "get_global_default_values",
    "get_kwarg_param",
    "get_non_required_value",
    "get_random_indices",
    "get_required_value",
    "is_in_ipython_session",
    "print_deprecation_message",
    "REQUIRED_VALUE",
    "reset_default_values",
    "set_default_value",
    "Singleton",
    "verify_and_resolve_path",
    "warn_if_set",
    "YamlLoadable",
]
