# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Catalog sub-package - registry/wire-format types for scenarios, initializers,
and targets that the PyRIT REST API exposes to external clients.

These models describe canonical PyRIT entities (a registered scenario, a
registered initializer, a runtime target instance, a scenario run summary)
and are imported by both the backend (as response/request payloads) and the
CLI (and any future external REST client). REST framing types (pagination
envelopes, RFC 7807 problem details, GUI-only request bodies) stay in
``pyrit.backend.models``
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.catalog.initializer import RegisteredInitializer
    from pyrit.models.catalog.scenario import (
        AttackErrorSummary,
        AttackRetrySummary,
        RegisteredScenario,
        RunScenarioRequest,
        ScenarioDatasetSizeCap,
        ScenarioDatasetSummary,
        ScenarioRunListItem,
        ScenarioRunSizeComponent,
        ScenarioRunSizeEstimate,
        ScenarioRunSizeEstimateRequest,
        ScenarioRunSummary,
        ScenarioTechniqueSummary,
    )
    from pyrit.models.catalog.target import TargetInstance

_LAZY_EXPORTS: dict[str, str] = {
    "AttackErrorSummary": "pyrit.models.catalog.scenario",
    "AttackRetrySummary": "pyrit.models.catalog.scenario",
    "RegisteredInitializer": "pyrit.models.catalog.initializer",
    "RegisteredScenario": "pyrit.models.catalog.scenario",
    "RunScenarioRequest": "pyrit.models.catalog.scenario",
    "ScenarioDatasetSizeCap": "pyrit.models.catalog.scenario",
    "ScenarioDatasetSummary": "pyrit.models.catalog.scenario",
    "ScenarioRunListItem": "pyrit.models.catalog.scenario",
    "ScenarioRunSizeComponent": "pyrit.models.catalog.scenario",
    "ScenarioRunSizeEstimate": "pyrit.models.catalog.scenario",
    "ScenarioRunSizeEstimateRequest": "pyrit.models.catalog.scenario",
    "ScenarioRunSummary": "pyrit.models.catalog.scenario",
    "ScenarioTechniqueSummary": "pyrit.models.catalog.scenario",
    "TargetInstance": "pyrit.models.catalog.target",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public catalog export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
