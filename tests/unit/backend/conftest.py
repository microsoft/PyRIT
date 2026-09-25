# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend compatibility fixtures independent of a packaged workspace stamp."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from pyrit import _compatibility
from pyrit.backend.main import app


@pytest.fixture(autouse=True)
def compatibility_id() -> Iterator[str]:
    """Supply startup provenance and initialize clients that deliberately skip lifespan."""
    identity = "0.14.0+g" + "a" * 40
    with (
        patch.object(_compatibility, "get_compatibility_id", return_value=identity),
        patch.object(app.state, "compatibility_id", identity, create=True),
    ):
        yield identity


@pytest.fixture
def compatibility_headers(compatibility_id: str) -> dict[str, str]:
    """Provide the marker required by business API requests."""
    return {_compatibility.COMPATIBILITY_HEADER: compatibility_id}
