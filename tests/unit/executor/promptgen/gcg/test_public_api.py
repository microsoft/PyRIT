# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the top-level :mod:`pyrit.executor.promptgen.gcg` public API surface."""

import pytest

# GCG, GCGGenerator, GCGContext, GCGResult, and load_goals_and_targets are
# torch-dependent (resolved via PEP 562 __getattr__ in the package __init__).
# Skip the whole file on installs that only have the base `dev` extra.
pytest.importorskip("torch", reason="GCG public API exposes torch-dependent symbols")

import pyrit.executor.promptgen.gcg as gcg_pkg
from pyrit.executor.promptgen.gcg import (
    GCG,
    GCGAlgorithmConfig,
    GCGConfig,
    GCGContext,
    GCGDataConfig,
    GCGGenerator,
    GCGModelConfig,
    GCGOutputConfig,
    GCGResult,
    GCGStrategyConfig,
    load_goals_and_targets,
)


def test_gcg_alias_is_gcg_generator() -> None:
    assert GCG is GCGGenerator


def test_public_api_symbols_are_exported() -> None:
    expected = {
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
        "load_goals_and_targets",
    }
    assert expected.issubset(set(gcg_pkg.__all__))


def test_public_api_symbols_are_importable_from_package() -> None:
    # Smoke-test that the imports at module top resolved to real objects so the
    # short import path (e.g. ``from pyrit.executor.promptgen.gcg import GCG``)
    # stays stable.
    symbols = (
        GCG,
        GCGAlgorithmConfig,
        GCGConfig,
        GCGContext,
        GCGDataConfig,
        GCGGenerator,
        GCGModelConfig,
        GCGOutputConfig,
        GCGResult,
        GCGStrategyConfig,
        load_goals_and_targets,
    )
    for sym in symbols:
        assert sym is not None
