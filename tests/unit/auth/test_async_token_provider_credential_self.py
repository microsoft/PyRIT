# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""``AsyncTokenProviderCredential.__aenter__`` is annotated ``-> Self``."""

from typing import assert_type

from pyrit.auth import AsyncTokenProviderCredential


class _DerivedCredential(AsyncTokenProviderCredential):
    pass


async def test_aenter_preserves_subclass_type_async() -> None:
    credential = _DerivedCredential(lambda: "token")

    async with credential as entered:
        assert_type(entered, _DerivedCredential)
        assert entered is credential
