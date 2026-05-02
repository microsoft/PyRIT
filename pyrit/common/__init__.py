# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Common utilities and helpers for PyRIT.

Heavy submodules (data_url_converter, display_response, download_hf_model,
net_utility) are intentionally NOT re-exported here to keep ``import pyrit``
fast.  Import them directly, e.g.::

    from pyrit.common.net_utility import get_httpx_client
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
from pyrit.common.default_values import get_non_required_value, get_required_value
from pyrit.common.deprecation import print_deprecation_message
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

__all__ = [
    "apply_defaults",
    "apply_defaults_to_method",
    "combine_dict",
    "combine_list",
    "DefaultValueScope",
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
