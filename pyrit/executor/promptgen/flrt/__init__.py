# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401, N814

"""
Public API for the Fluent Student-Teacher Redteaming adversarial-prompt generator.

This package is experimental. Its APIs can change in any release without a
deprecation cycle.
"""

import warnings
from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export
from pyrit.exceptions import ExperimentalWarning

if TYPE_CHECKING:
    from pyrit.executor.promptgen.flrt.config import (
        FLRTAlgorithmConfig,
        FLRTModelConfig,
        FLRTOutputConfig,
    )
    from pyrit.executor.promptgen.flrt.edit_operations import (
        UpdateType,
        choose_update_type,
        constrain_update_probabilities,
        propose_candidates,
    )
    from pyrit.executor.promptgen.flrt.generator import (
        FLRTContext,
        FLRTGenerator,
        FLRTResult,
    )
    from pyrit.executor.promptgen.flrt.generator import (
        FLRTGenerator as FLRT,
    )
    from pyrit.executor.promptgen.flrt.objective import (
        clamped_forcing_loss,
        fluency_loss,
        per_token_nll,
        repetition_loss,
        total_loss,
    )

warnings.warn(
    "pyrit.executor.promptgen.flrt is experimental: APIs may change in any release "
    "without a deprecation cycle. Pin pyrit to a specific version if you depend "
    "on this module. To silence: "
    "warnings.filterwarnings('ignore', category=pyrit.exceptions.ExperimentalWarning).",
    ExperimentalWarning,
    stacklevel=2,
)

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "FLRT": ("pyrit.executor.promptgen.flrt.generator", "FLRTGenerator"),
    "FLRTAlgorithmConfig": "pyrit.executor.promptgen.flrt.config",
    "FLRTContext": "pyrit.executor.promptgen.flrt.generator",
    "FLRTGenerator": "pyrit.executor.promptgen.flrt.generator",
    "FLRTModelConfig": "pyrit.executor.promptgen.flrt.config",
    "FLRTOutputConfig": "pyrit.executor.promptgen.flrt.config",
    "FLRTResult": "pyrit.executor.promptgen.flrt.generator",
    "UpdateType": "pyrit.executor.promptgen.flrt.edit_operations",
    "choose_update_type": "pyrit.executor.promptgen.flrt.edit_operations",
    "clamped_forcing_loss": "pyrit.executor.promptgen.flrt.objective",
    "constrain_update_probabilities": "pyrit.executor.promptgen.flrt.edit_operations",
    "fluency_loss": "pyrit.executor.promptgen.flrt.objective",
    "per_token_nll": "pyrit.executor.promptgen.flrt.objective",
    "propose_candidates": "pyrit.executor.promptgen.flrt.edit_operations",
    "repetition_loss": "pyrit.executor.promptgen.flrt.objective",
    "total_loss": "pyrit.executor.promptgen.flrt.objective",
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
