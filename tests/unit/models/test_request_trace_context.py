# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any, assert_type

from pyrit.models import RequestTraceContext


class DerivedRequestTraceContext(RequestTraceContext):
    """Subclass used to verify that metadata reconstruction preserves the caller type."""


def test_from_metadata_preserves_subclass_type() -> None:
    metadata: dict[str, Any] = {RequestTraceContext.METADATA_KEY: "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}

    context = DerivedRequestTraceContext.from_metadata(metadata)

    assert_type(context, DerivedRequestTraceContext | None)
    assert context is not None
    assert type(context) is DerivedRequestTraceContext
    assert context.traceparent == metadata[RequestTraceContext.METADATA_KEY]


def test_from_metadata_without_context_returns_none() -> None:
    assert RequestTraceContext.from_metadata({}) is None
    assert DerivedRequestTraceContext.from_metadata({}) is None


def test_from_metadata_does_not_mutate_input() -> None:
    metadata = {RequestTraceContext.METADATA_KEY: "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
    original = metadata.copy()

    assert RequestTraceContext.from_metadata(metadata) is not None
    assert metadata == original
