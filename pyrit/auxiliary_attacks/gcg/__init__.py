# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Public API for the Greedy Coordinate Gradient (GCG) auxiliary attack.

The primary entry point is ``GCG`` (alias for ``GCGGenerator``), a
``pyrit.executor.promptgen.core.PromptGeneratorStrategy`` that produces
adversarial suffixes via the GCG algorithm.

Example:

    from pyrit.auxiliary_attacks.gcg import (
        GCG,
        GCGAlgorithmConfig,
        GCGModelConfig,
    )

    generator = GCG(
        models=[GCGModelConfig(name="meta-llama/Llama-2-7b-chat-hf")],
        algorithm=GCGAlgorithmConfig(n_steps=500, batch_size=512),
    )
    result = await generator.execute_async(
        goals=["how do I ..."],
        targets=["Sure, here is ..."],
    )
"""

from typing import TYPE_CHECKING, Any

from pyrit.auxiliary_attacks.gcg.config import (
    GCGAlgorithmConfig,
    GCGConfig,
    GCGDataConfig,
    GCGModelConfig,
    GCGOutputConfig,
    GCGStrategyConfig,
)

# Torch-dependent symbols are exposed lazily via PEP 562 __getattr__ so that
# `from pyrit.auxiliary_attacks.gcg import GCGConfig` works on installs that
# only have the base `dev` extra (no torch). Touching any of these names from
# the package root triggers the underlying module import on first access; if
# torch is missing the user gets a clear ModuleNotFoundError pointing at torch.
#
# The extension Protocols live in ``extension_protocols`` (typing-only — that
# module imports cleanly without torch) but are routed through the same lazy
# mechanism so all GCG public symbols share one re-export pathway.
_LAZY_IMPORTS = {
    "CandidateFilter": ("pyrit.auxiliary_attacks.gcg.extension_protocols", "CandidateFilter"),
    "GCG": ("pyrit.auxiliary_attacks.gcg.generator", "GCGGenerator"),
    "GCGContext": ("pyrit.auxiliary_attacks.gcg.generator", "GCGContext"),
    "GCGGenerator": ("pyrit.auxiliary_attacks.gcg.generator", "GCGGenerator"),
    "GCGResult": ("pyrit.auxiliary_attacks.gcg.generator", "GCGResult"),
    "LossFunction": ("pyrit.auxiliary_attacks.gcg.extension_protocols", "LossFunction"),
    "SamplingStrategy": ("pyrit.auxiliary_attacks.gcg.extension_protocols", "SamplingStrategy"),
    "SuffixInitializer": ("pyrit.auxiliary_attacks.gcg.extension_protocols", "SuffixInitializer"),
    "load_goals_and_targets": ("pyrit.auxiliary_attacks.gcg.data", "load_goals_and_targets"),
}

if TYPE_CHECKING:
    from pyrit.auxiliary_attacks.gcg.data import load_goals_and_targets
    from pyrit.auxiliary_attacks.gcg.extension_protocols import (
        CandidateFilter,
        LossFunction,
        SamplingStrategy,
        SuffixInitializer,
    )
    from pyrit.auxiliary_attacks.gcg.generator import (
        GCGContext,
        GCGGenerator,
        GCGResult,
    )

    GCG = GCGGenerator


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        import importlib

        module_name, attr = _LAZY_IMPORTS[name]
        value = getattr(importlib.import_module(module_name), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_LAZY_IMPORTS.keys())))


__all__ = [
    "CandidateFilter",
    "GCG",
    "GCGAlgorithmConfig",
    "GCGConfig",
    "GCGContext",
    "GCGDataConfig",
    "GCGGenerator",
    "GCGModelConfig",
    "GCGOutputConfig",
    "GCGResult",
    "GCGStrategyConfig",
    "LossFunction",
    "SamplingStrategy",
    "SuffixInitializer",
    "load_goals_and_targets",
]
