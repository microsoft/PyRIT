# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""PyRIT initializers package."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.parameter import Parameter
    from pyrit.setup.initializers.load_default_datasets import LoadDefaultDatasets
    from pyrit.setup.initializers.preload_scenario_metadata import PreloadScenarioMetadata
    from pyrit.setup.initializers.refresh_datasets import RefreshDatasets
    from pyrit.setup.initializers.scorers import ScorerInitializer
    from pyrit.setup.initializers.targets import TargetInitializer
    from pyrit.setup.initializers.techniques import TechniqueInitializer
    from pyrit.setup.pyrit_initializer import PyRITInitializer

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "Parameter": "pyrit.models.parameter",
    "PyRITInitializer": "pyrit.setup.pyrit_initializer",
    "TechniqueInitializer": "pyrit.setup.initializers.techniques",
    "ScorerInitializer": "pyrit.setup.initializers.scorers",
    "TargetInitializer": "pyrit.setup.initializers.targets",
    "LoadDefaultDatasets": "pyrit.setup.initializers.load_default_datasets",
    "PreloadScenarioMetadata": "pyrit.setup.initializers.preload_scenario_metadata",
    "RefreshDatasets": "pyrit.setup.initializers.refresh_datasets",
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
