# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

from pyrit.auth import caller_principal_context, caller_principal_scope, get_caller_principal


def test_caller_principal_scope_sets_and_restores() -> None:
    assert get_caller_principal() is None
    with caller_principal_scope("alice"):
        assert get_caller_principal() == "alice"
        with caller_principal_scope(None):
            assert get_caller_principal() is None
        assert get_caller_principal() == "alice"
    assert get_caller_principal() is None


async def test_caller_principal_context_overrides_inherited_caller_async() -> None:
    async def read_principal_async() -> str | None:
        return get_caller_principal()

    with caller_principal_scope("alice"):
        inherited = await asyncio.create_task(read_principal_async())
        replaced = await asyncio.create_task(read_principal_async(), context=caller_principal_context("bob"))
        cleared = await asyncio.create_task(read_principal_async(), context=caller_principal_context(None))
        assert get_caller_principal() == "alice"

    assert (inherited, replaced, cleared) == ("alice", "bob", None)
