# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401, N814

"""
Public API for the Greedy Coordinate Gradient adversarial-suffix generator.

This package is experimental. Its APIs can change in any release without a
deprecation cycle.
"""

import warnings
from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export
from pyrit.exceptions import ExperimentalWarning

if TYPE_CHECKING:
    from pyrit.executor.promptgen.gcg.config import (
        GCGAlgorithmConfig,
        GCGConfig,
        GCGDataConfig,
        GCGModelConfig,
        GCGOutputConfig,
        GCGStrategyConfig,
    )
    from pyrit.executor.promptgen.gcg.data import load_goals_and_targets
    from pyrit.executor.promptgen.gcg.default_implementations import (
        CrossEntropyLoss,
        LengthPreservingFilter,
        LiteralStringInit,
        StandardGCGSampling,
    )
    from pyrit.executor.promptgen.gcg.extension_protocols import (
        CandidateFilter,
        LossFunction,
        SamplingStrategy,
        SuffixInitializer,
    )
    from pyrit.executor.promptgen.gcg.generator import (
        GCGContext,
        GCGGenerator,
        GCGResult,
    )
    from pyrit.executor.promptgen.gcg.generator import (
        GCGGenerator as GCG,
    )

warnings.warn(
    "pyrit.executor.promptgen.gcg is experimental: APIs may change in any release "
    "without a deprecation cycle. Pin pyrit to a specific version if you depend "
    "on this module. To silence: "
    "warnings.filterwarnings('ignore', category=pyrit.exceptions.ExperimentalWarning).",
    ExperimentalWarning,
    stacklevel=2,
)

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "CandidateFilter": "pyrit.executor.promptgen.gcg.extension_protocols",
    "CrossEntropyLoss": "pyrit.executor.promptgen.gcg.default_implementations",
    "GCG": ("pyrit.executor.promptgen.gcg.generator", "GCGGenerator"),
    "GCGAlgorithmConfig": "pyrit.executor.promptgen.gcg.config",
    "GCGConfig": "pyrit.executor.promptgen.gcg.config",
    "GCGContext": "pyrit.executor.promptgen.gcg.generator",
    "GCGDataConfig": "pyrit.executor.promptgen.gcg.config",
    "GCGGenerator": "pyrit.executor.promptgen.gcg.generator",
    "GCGModelConfig": "pyrit.executor.promptgen.gcg.config",
    "GCGOutputConfig": "pyrit.executor.promptgen.gcg.config",
    "GCGResult": "pyrit.executor.promptgen.gcg.generator",
    "GCGStrategyConfig": "pyrit.executor.promptgen.gcg.config",
    "LengthPreservingFilter": "pyrit.executor.promptgen.gcg.default_implementations",
    "LiteralStringInit": "pyrit.executor.promptgen.gcg.default_implementations",
    "LossFunction": "pyrit.executor.promptgen.gcg.extension_protocols",
    "SamplingStrategy": "pyrit.executor.promptgen.gcg.extension_protocols",
    "StandardGCGSampling": "pyrit.executor.promptgen.gcg.default_implementations",
    "SuffixInitializer": "pyrit.executor.promptgen.gcg.extension_protocols",
    "load_goals_and_targets": "pyrit.executor.promptgen.gcg.data",
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
