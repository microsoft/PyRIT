# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Benchmark scenario classes."""

from typing import Any

from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark, _build_benchmark_strategy


def __getattr__(name: str) -> Any:
    """
    Lazily resolve the dynamic BenchmarkStrategy class.

    Returns:
        Any: The resolved strategy class.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "AdversarialBenchmarkStrategy":
        return _build_benchmark_strategy()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AdversarialBenchmark", "AdversarialBenchmarkStrategy"]
