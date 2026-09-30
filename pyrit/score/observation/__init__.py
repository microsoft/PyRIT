# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Acquire scoring evidence and support replay without performing evaluation."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.score.observation.execution import NonReplayableObservationError
    from pyrit.score.observation.observation_source import ObservationSource
    from pyrit.score.observation.otel_span_exporter import InMemoryTraceExporter
    from pyrit.score.observation.otel_trace_source import OtelTraceSource
    from pyrit.score.observation.trace_client import InMemoryTraceClient, TraceAcquisitionError, TraceClient

_LAZY_EXPORTS: dict[str, str] = {
    "InMemoryTraceClient": "pyrit.score.observation.trace_client",
    "InMemoryTraceExporter": "pyrit.score.observation.otel_span_exporter",
    "NonReplayableObservationError": "pyrit.score.observation.execution",
    "ObservationSource": "pyrit.score.observation.observation_source",
    "OtelTraceSource": "pyrit.score.observation.otel_trace_source",
    "TraceAcquisitionError": "pyrit.score.observation.trace_client",
    "TraceClient": "pyrit.score.observation.trace_client",
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
