# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import copy
import inspect
import pickle

import pytest

from pyrit.auth import (
    CallerCredentialRequiredError,
    CallerCredentialStore,
    caller_principal_context,
    caller_principal_scope,
    create_caller_credential_provider,
    ensure_async_token_provider,
    get_caller_principal,
    is_caller_credential_provider,
)

_SECRET = "fake-secret-marker"


@pytest.fixture
def store() -> CallerCredentialStore:
    return CallerCredentialStore()


def test_store_set_get_and_replace(store: CallerCredentialStore) -> None:
    store.set_credential(principal="alice", credential_id="target", credential="first")
    store.set_credential(principal="alice", credential_id="target", credential="second")

    assert store.get_credential(principal="alice", credential_id="target") == "second"
    assert store.has_credential(principal="alice", credential_id="target")
    assert not store.has_credential(principal="bob", credential_id="target")
    assert len(store) == 1


@pytest.mark.parametrize("credential", ["", "   ", None, 123])
def test_store_rejects_blank_or_non_string_credentials(store: CallerCredentialStore, credential: object) -> None:
    with pytest.raises(ValueError, match="nonempty string"):
        store.set_credential(principal="alice", credential_id="target", credential=credential)  # type: ignore[arg-type]


@pytest.mark.parametrize(("principal", "credential_id"), [("", "target"), ("alice", "")])
def test_store_requires_principal_and_credential_id(
    store: CallerCredentialStore, principal: str, credential_id: str
) -> None:
    with pytest.raises(ValueError, match="principal and credential ID"):
        store.set_credential(principal=principal, credential_id=credential_id, credential="value")


def test_store_remove_credential_only_affects_one_caller(store: CallerCredentialStore) -> None:
    store.set_credential(principal="alice", credential_id="target", credential="a")
    store.set_credential(principal="bob", credential_id="target", credential="b")

    assert store.remove_credential(principal="alice", credential_id="target")
    assert not store.remove_credential(principal="alice", credential_id="target")
    assert store.get_credential(principal="bob", credential_id="target") == "b"


def test_store_remove_credential_id_forgets_every_caller(store: CallerCredentialStore) -> None:
    store.set_credential(principal="alice", credential_id="target", credential="a")
    store.set_credential(principal="bob", credential_id="target", credential="b")
    store.set_credential(principal="alice", credential_id="other", credential="c")

    store.remove_credential_id("target")

    assert len(store) == 1
    assert store.get_credential(principal="alice", credential_id="other") == "c"


def test_store_clear(store: CallerCredentialStore) -> None:
    store.set_credential(principal="alice", credential_id="target", credential="a")

    store.clear()

    assert len(store) == 0


def test_store_never_reveals_or_duplicates_credentials(store: CallerCredentialStore) -> None:
    store.set_credential(principal="alice", credential_id="target", credential=_SECRET)

    assert _SECRET not in repr(store)
    assert copy.copy(store) is store
    assert copy.deepcopy({"store": store})["store"] is store
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(store)


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


async def test_provider_returns_each_callers_own_credential_async(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")
    store.set_credential(principal="alice", credential_id="target", credential="alice-key")
    store.set_credential(principal="bob", credential_id="target", credential="bob-key")

    with caller_principal_scope("alice"):
        alice_key = await provider()
    with caller_principal_scope("bob"):
        bob_key = await provider()

    assert (alice_key, bob_key) == ("alice-key", "bob-key")


async def test_provider_reads_replacement_on_next_call_async(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")
    store.set_credential(principal="alice", credential_id="target", credential="old")

    with caller_principal_scope("alice"):
        first = await provider()
        store.set_credential(principal="alice", credential_id="target", credential="new")
        second = await provider()

    assert (first, second) == ("old", "new")


async def test_provider_without_caller_fails_closed_async(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")
    store.set_credential(principal="alice", credential_id="target", credential="alice-key")

    with pytest.raises(CallerCredentialRequiredError, match="no caller identity"):
        await provider()


async def test_provider_without_callers_credential_fails_closed_async(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")
    store.set_credential(principal="alice", credential_id="target", credential=_SECRET)

    with caller_principal_scope("bob"), pytest.raises(CallerCredentialRequiredError) as exc_info:
        await provider()

    assert "'shared'" in str(exc_info.value)
    assert _SECRET not in str(exc_info.value)


async def test_provider_after_removal_fails_closed_async(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")
    store.set_credential(principal="alice", credential_id="target", credential="alice-key")
    store.remove_credential_id("target")

    with caller_principal_scope("alice"), pytest.raises(CallerCredentialRequiredError):
        await provider()


def test_provider_is_an_async_token_provider(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")

    assert inspect.iscoroutinefunction(provider)
    assert ensure_async_token_provider(provider) is provider
    assert copy.deepcopy(provider) is provider


def test_only_caller_credential_providers_are_recognized(store: CallerCredentialStore) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="target", component_name="shared")

    async def own_provider() -> str:
        return "own-key"

    assert is_caller_credential_provider(provider)
    assert is_caller_credential_provider(ensure_async_token_provider(provider))
    assert is_caller_credential_provider(copy.deepcopy(provider))
    assert not is_caller_credential_provider(own_provider)
    assert not is_caller_credential_provider(ensure_async_token_provider(lambda: "own-key"))
    assert not is_caller_credential_provider("own-key")
    assert not is_caller_credential_provider(None)
    assert not is_caller_credential_provider({"api_key": "own-key"})
