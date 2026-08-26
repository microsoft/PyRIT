# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario implementations package."""

import importlib
import pkgutil


def _materialize_scenarios() -> None:
    """Import every built-in scenario module for complete registry discovery."""
    prefix = f"{__name__}."
    for module_info in pkgutil.walk_packages(__path__, prefix):
        importlib.import_module(module_info.name)
