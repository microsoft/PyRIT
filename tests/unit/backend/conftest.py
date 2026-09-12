# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend compatibility fixtures independent of a packaged workspace stamp."""

import pytest

from pyrit import _compatibility


@pytest.fixture(autouse=True)
def compatibility_id(monkeypatch: pytest.MonkeyPatch) -> str:
    """Use deterministic provenance without changing validation behavior."""
    identity = "0.14.0+g" + "a" * 40
    monkeypatch.setattr(_compatibility, "get_compatibility_id", lambda: identity)
    return identity


@pytest.fixture
def compatibility_headers(compatibility_id: str) -> dict[str, str]:
    """Provide the marker required by business API requests."""
    return {_compatibility.COMPATIBILITY_HEADER: compatibility_id}
