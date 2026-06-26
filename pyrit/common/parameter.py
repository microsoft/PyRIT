# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — the parameter contract now lives in ``pyrit.models.parameter``.

Importing names from ``pyrit.common.parameter`` still works for one release but
emits a one-time ``DeprecationWarning`` per name. Import from
``pyrit.models.parameter`` (or ``pyrit.models``) instead. This shim will be
removed in 0.16.0.

NOTE: When this shim is removed, also drop the ``pyrit.common.parameter`` entry
from ``KNOWN_COMMON_VIOLATIONS`` in ``tests/unit/models/test_import_boundary.py``
if it has not already been removed.
"""

from __future__ import annotations

from pyrit.common.deprecation import module_deprecation_getattr

__all__ = [
    "ComponentType",
    "Parameter",
    "ParameterDestination",
    "RegistryReference",
]

__getattr__ = module_deprecation_getattr(
    old_module="pyrit.common.parameter",
    target_module="pyrit.models.parameter",
    names=__all__,
    removed_in="0.16.0",
)


def __dir__() -> list[str]:
    return sorted(__all__)
