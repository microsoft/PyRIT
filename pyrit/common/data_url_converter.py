# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — the data-URL conversion helpers now live in
``pyrit.memory.storage``.

Importing names from ``pyrit.common.data_url_converter`` still works for one
release but emits a one-time ``DeprecationWarning`` per name. Import from
``pyrit.memory.storage`` instead. This shim will be removed in 0.16.0.

NOTE: When this shim is removed, also drop the ``pyrit.common.data_url_converter``
entry from ``KNOWN_COMMON_VIOLATIONS`` in
``tests/unit/models/test_import_boundary.py`` if it has not already been removed,
so the reverse-guard ratchet bookkeeping is not missed.
"""

from __future__ import annotations

from pyrit.common.deprecation import module_deprecation_getattr

__all__ = [
    "AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS",
    "convert_local_image_to_data_url",
    "convert_local_image_to_data_url_async",
]

__getattr__ = module_deprecation_getattr(
    old_module="pyrit.common.data_url_converter",
    target_module="pyrit.memory.storage.data_url_converter",
    names=__all__,
    removed_in="0.16.0",
)


def __dir__() -> list[str]:
    return sorted(__all__)
