# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Backend services module.

Provides business logic layer for API routes.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.backend.services.attack_service import AttackService, get_attack_service
    from pyrit.backend.services.converter_service import ConverterService, get_converter_service
    from pyrit.backend.services.dataset_service import DatasetService, get_dataset_service
    from pyrit.backend.services.initializer_service import InitializerService, get_initializer_service
    from pyrit.backend.services.scenario_run_service import ScenarioRunService, get_scenario_run_service
    from pyrit.backend.services.scenario_service import ScenarioService, get_scenario_service
    from pyrit.backend.services.target_service import TargetService, get_target_service

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AttackService": "pyrit.backend.services.attack_service",
    "get_attack_service": "pyrit.backend.services.attack_service",
    "ConverterService": "pyrit.backend.services.converter_service",
    "get_converter_service": "pyrit.backend.services.converter_service",
    "DatasetService": "pyrit.backend.services.dataset_service",
    "get_dataset_service": "pyrit.backend.services.dataset_service",
    "InitializerService": "pyrit.backend.services.initializer_service",
    "get_initializer_service": "pyrit.backend.services.initializer_service",
    "ScenarioService": "pyrit.backend.services.scenario_service",
    "get_scenario_service": "pyrit.backend.services.scenario_service",
    "ScenarioRunService": "pyrit.backend.services.scenario_run_service",
    "get_scenario_run_service": "pyrit.backend.services.scenario_run_service",
    "TargetService": "pyrit.backend.services.target_service",
    "get_target_service": "pyrit.backend.services.target_service",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
