# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Benchmark scenario classes."""

from typing import Any

from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark, _build_benchmark_technique


def __getattr__(name: str) -> Any:
    """
    Lazily resolve the dynamic BenchmarkTechnique class.

    Returns:
        Any: The resolved technique class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "AdversarialBenchmarkTechnique":
        return _build_benchmark_technique()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AdversarialBenchmark", "AdversarialBenchmarkTechnique"]
