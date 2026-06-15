# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Component registries package.

This package contains registries for PyRIT components (objects identified by a
``ComponentIdentifier``, such as converters, scorers, and targets). A component
registry is a ``BuildableRegistry`` class catalog that can build instances from
classes and, when it retains pre-configured instances, also exposes them via an
``.instances`` property.

Shared capabilities and base classes (``BuildableRegistry``, ``InstanceRegistry``,
``DefaultInstanceRegistry``) live at the top level of ``pyrit.registry``.
"""

from pyrit.registry.components.converter_registry import (
    ConverterMetadata,
    ConverterRegistry,
)

__all__ = [
    "ConverterRegistry",
    "ConverterMetadata",
]
