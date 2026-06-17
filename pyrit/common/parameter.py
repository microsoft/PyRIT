# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Compatibility shim for the canonical model-side parameter contract."""

from pyrit.models.parameter import (
    ComponentRegistryKind,
    Parameter,
    ParameterDestination,
    RegistryReference,
    coerce_bool,
    coerce_list,
    coerce_scalar,
    coerce_value,
    validate_param_type,
)

__all__ = [
    "ComponentRegistryKind",
    "Parameter",
    "ParameterDestination",
    "RegistryReference",
    "coerce_bool",
    "coerce_list",
    "coerce_scalar",
    "coerce_value",
    "validate_param_type",
]
