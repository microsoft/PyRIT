# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Common utilities and helpers for PyRIT."""

import importlib
from typing import TYPE_CHECKING

from pyrit.common.apply_defaults import (
    REQUIRED_VALUE,
    DefaultValueScope,
    apply_defaults,
    apply_defaults_to_method,
    get_global_default_values,
    reset_default_values,
    set_default_value,
)
from pyrit.common.data_url_converter import convert_local_image_to_data_url
from pyrit.common.default_values import get_non_required_value, get_required_value
from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.display_response import display_image_response
from pyrit.common.net_utility import get_httpx_client, make_request_and_raise_if_error_async
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

if TYPE_CHECKING:
    from pyrit.common.download_hf_model import (
        download_chunk,
        download_file,
        download_files,
        download_specific_files,
        get_available_files,
    )

# Lazy imports for modules with heavy third-party dependencies (PEP 562).
# download_hf_model imports `huggingface_hub` which adds ~0.3s to startup.
_LAZY_IMPORTS: dict[str, str] = {
    "download_chunk": "pyrit.common.download_hf_model",
    "download_file": "pyrit.common.download_hf_model",
    "download_files": "pyrit.common.download_hf_model",
    "download_specific_files": "pyrit.common.download_hf_model",
    "get_available_files": "pyrit.common.download_hf_model",
}


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module = importlib.import_module(_LAZY_IMPORTS[name])
        attr = getattr(module, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "apply_defaults",
    "apply_defaults_to_method",
    "combine_dict",
    "combine_list",
    "convert_local_image_to_data_url",
    "DefaultValueScope",
    "display_image_response",
    "download_chunk",
    "download_file",
    "download_files",
    "download_specific_files",
    "get_available_files",
    "get_global_default_values",
    "get_httpx_client",
    "get_kwarg_param",
    "get_non_required_value",
    "get_random_indices",
    "get_required_value",
    "verify_and_resolve_path",
    "is_in_ipython_session",
    "make_request_and_raise_if_error_async",
    "REQUIRED_VALUE",
    "reset_default_values",
    "set_default_value",
    "Singleton",
    "warn_if_set",
    "YamlLoadable",
    "print_deprecation_message",
]
