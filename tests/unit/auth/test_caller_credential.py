# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import copy
import inspect
import pickle

import pytest

from pyrit.auth import (
    CallerCredential,
    CallerCredentialRequiredError,
    caller_principal_context,
    caller_principal_scope,
    ensure_async_token_provider,
    get_caller_principal,
    resolve_caller_credential,
    validate_token_provider_headers,
)

_SECRET = "fake-secret-marker"


def test_set_replace_and_check_one_callers_key() -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential="first")
    credential.set_credential(principal="alice", credential="second")

    assert credential.has_credential(principal="alice")
    assert not credential.has_credential(principal="bob")


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_set_credential_rejects_blank_or_non_string_keys(value: object) -> None:
    with pytest.raises(ValueError, match="nonempty string"):
        CallerCredential().set_credential(principal="alice", credential=value)  # type: ignore[arg-type]


def test_set_credential_requires_a_principal() -> None:
    with pytest.raises(ValueError, match="principal is required"):
        CallerCredential().set_credential(principal="", credential="value")


def test_remove_credential_only_affects_one_caller() -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential="a")
    credential.set_credential(principal="bob", credential="b")

    assert credential.remove_credential(principal="alice")
    assert not credential.remove_credential(principal="alice")
    assert credential.has_credential(principal="bob")


def test_credential_never_reveals_or_duplicates_keys() -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential=_SECRET)

    assert _SECRET not in repr(credential)
    assert copy.copy(credential) is credential
    assert copy.deepcopy({"credential": credential})["credential"] is credential
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(credential)


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


async def test_get_credential_returns_each_callers_own_key_async() -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential="alice-key")
    credential.set_credential(principal="bob", credential="bob-key")

    with caller_principal_scope("alice"):
        alice_key = await credential.get_credential_async()
    with caller_principal_scope("bob"):
        bob_key = await credential.get_credential_async()

    assert (alice_key, bob_key) == ("alice-key", "bob-key")


async def test_get_credential_reads_a_replaced_key_on_the_next_call_async() -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential="old")

    with caller_principal_scope("alice"):
        first = await credential.get_credential_async()
        credential.set_credential(principal="alice", credential="new")
        second = await credential.get_credential_async()

    assert (first, second) == ("old", "new")


async def test_get_credential_without_a_caller_fails_closed_async() -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential="alice-key")

    with pytest.raises(CallerCredentialRequiredError, match="no signed-in caller"):
        await credential.get_credential_async()


@pytest.mark.parametrize("remove", [False, True])
async def test_get_credential_without_the_callers_key_fails_closed_async(remove: bool) -> None:
    credential = CallerCredential()
    credential.set_credential(principal="alice", credential=_SECRET)
    if remove:
        credential.remove_credential(principal="alice")

    with caller_principal_scope("alice" if remove else "bob"), pytest.raises(CallerCredentialRequiredError) as exc:
        await credential.get_credential_async()

    assert "have not set yours" in str(exc.value)
    assert _SECRET not in str(exc.value)


def test_get_credential_is_an_async_token_provider() -> None:
    provider = CallerCredential().get_credential_async

    assert inspect.iscoroutinefunction(provider)
    assert ensure_async_token_provider(provider) is provider


@pytest.mark.parametrize(
    ("principal", "has_key", "missing"), [(None, False, False), ("alice", False, True), ("alice", True, False)]
)
def test_is_missing_for_current_caller(principal: str | None, has_key: bool, missing: bool) -> None:
    credential = CallerCredential()
    if has_key:
        credential.set_credential(principal="alice", credential="alice-key")

    with caller_principal_scope(principal):
        assert credential.is_missing_for_current_caller() is missing


def test_resolve_caller_credential_creates_or_reuses_a_credential_for_caller_mode() -> None:
    existing = CallerCredential()

    created = resolve_caller_credential(auth_mode="caller", api_key=None)

    assert isinstance(created, CallerCredential)
    assert created is not existing
    assert resolve_caller_credential(auth_mode="caller", api_key=existing) is existing


@pytest.mark.parametrize("api_key", ["a-key", lambda: "a-token"])
def test_resolve_caller_credential_rejects_a_key_in_caller_mode(api_key: object) -> None:
    with pytest.raises(ValueError, match="cannot be combined with an explicit api_key"):
        resolve_caller_credential(auth_mode="caller", api_key=api_key)


@pytest.mark.parametrize("auth_mode", ["api_key", "identity", "none"])
def test_resolve_caller_credential_is_none_for_other_modes(auth_mode: str) -> None:
    assert resolve_caller_credential(auth_mode=auth_mode, api_key="a-key") is None
    with pytest.raises(ValueError, match='requires auth_mode="caller"'):
        resolve_caller_credential(auth_mode=auth_mode, api_key=CallerCredential())


@pytest.mark.parametrize("header", ["Authorization", "proxy-authorization", "api-key", "X-API-Key", "x-goog-api-key"])
def test_validate_token_provider_headers_rejects_static_authentication(header: str) -> None:
    with pytest.raises(ValueError, match="Static authentication headers"):
        validate_token_provider_headers({header: "value"})


def test_validate_token_provider_headers_allows_other_headers() -> None:
    validate_token_provider_headers(None)
    validate_token_provider_headers({"x-request-source": "pyrit"})
