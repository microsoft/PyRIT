# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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

from pyrit.models.catalog.initializer import (
    RegisteredInitializer,
)
from pyrit.models.catalog.scenario import (
    AttackErrorSummary,
    AttackRetrySummary,
    RegisteredScenario,
    RunScenarioRequest,
    ScenarioRunSummary,
)
from pyrit.models.catalog.target import (
    TargetInstance,
)

__all__ = [
    "AttackErrorSummary",
    "AttackRetrySummary",
    "RegisteredInitializer",
    "RegisteredScenario",
    "RunScenarioRequest",
    "ScenarioRunSummary",
    "TargetInstance",
]
