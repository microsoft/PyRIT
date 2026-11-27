# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Keep one fixture setup error from turning every later test that uses the fixture into an error.

pytest-asyncio wraps async fixture setup and requests its event loop runner before pytest's own
``pytest_fixture_setup`` runs. When the runner itself fails (for example, Windows cannot create the
loop's self-pipe socket pair and raises ``OSError: [WinError 10055]``), the fixture keeps the finalizer
pytest registered for it but never stores a cached result. pytest then skips that fixture's teardown,
and every later test that requests it fails in setup with ``assert not self._finalizers`` instead of
running. See https://github.com/pytest-dev/pytest/issues/14800.

The wrapper below caches the setup error the same way pytest's own hook does, so the original error is
still reported for the test that hit it, teardown clears the finalizer, and later tests run normally.

Remove this hook once the locked pytest contains a fix for that issue (proposed in
https://github.com/pytest-dev/pytest/pull/15275 and https://github.com/pytest-dev/pytest/pull/15241).
Keep ``tests/unit/test_async_fixture_setup_errors.py``; it proves the fix still holds without the hook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[object], request: pytest.FixtureRequest
) -> Generator[None, object, object]:
    """
    Cache a fixture setup error that an inner hook raised without caching it.

    Args:
        fixturedef (pytest.FixtureDef[object]): The fixture being set up.
        request (pytest.FixtureRequest): The request for that fixture.

    Returns:
        object: The fixture value from the inner hooks.

    Raises:
        Exception: The original setup error, unchanged, after caching it.
    """
    try:
        return (yield)
    except (Exception, pytest.skip.Exception, pytest.fail.Exception) as error:
        # Mirror pytest's default hook so FixtureDef.finish runs the pending finalizers at teardown.
        if fixturedef.cached_result is None:
            fixturedef.cached_result = (None, fixturedef.cache_key(request), (error, error.__traceback__))
        raise
