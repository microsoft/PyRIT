# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the hooks that bridge legacy ``MemoryInterface`` backends to the async query API."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface, SQLiteMemory


def _make_backend(*, overrides: dict[str, Any] | None = None) -> SQLiteMemory:
    """
    Build a backend instance without opening a database.

    The hooks under test only inspect the class hierarchy, so ``__init__`` is skipped on purpose. Tests call the
    ``MemoryInterface`` implementations directly because ``SQLiteMemory`` overrides some of them.

    Returns:
        SQLiteMemory: An instance of a subclass that applies ``overrides`` as class attributes.
    """
    backend_class = type("_LegacyBackend", (SQLiteMemory,), overrides or {})
    return backend_class.__new__(backend_class)


_LEGACY_QUERY_HOOKS = [
    pytest.param(
        "_execute_get_unique_attack_class_names",
        "get_unique_attack_class_names",
        {},
        "Implement the unique attack class query.",
        id="unique-attack-class-names",
    ),
    pytest.param(
        "_execute_get_unique_converter_class_names",
        "get_unique_converter_class_names",
        {},
        "Implement the unique converter class query.",
        id="unique-converter-class-names",
    ),
    pytest.param(
        "_execute_get_conversation_stats",
        "get_conversation_stats",
        {"conversation_ids": ["conversation-1"]},
        "Implement the conversation statistics query.",
        id="conversation-stats",
    ),
]


@pytest.mark.parametrize(("hook_name", "legacy_method_name", "kwargs", "expected_message"), _LEGACY_QUERY_HOOKS)
def test_legacy_query_hook_raises_when_backend_does_not_implement_query(
    *, hook_name: str, legacy_method_name: str, kwargs: dict[str, Any], expected_message: str
) -> None:
    backend = _make_backend()
    assert getattr(type(backend), legacy_method_name) is getattr(MemoryInterface, legacy_method_name)

    with pytest.raises(NotImplementedError, match=expected_message):
        getattr(MemoryInterface, hook_name)(backend, **kwargs)


@pytest.mark.parametrize(("hook_name", "legacy_method_name", "kwargs", "expected_message"), _LEGACY_QUERY_HOOKS)
def test_legacy_query_hook_delegates_to_backend_override(
    *, hook_name: str, legacy_method_name: str, kwargs: dict[str, Any], expected_message: str
) -> None:
    legacy_override = MagicMock(name=legacy_method_name)
    backend = _make_backend(overrides={legacy_method_name: legacy_override})

    result = getattr(MemoryInterface, hook_name)(backend, **kwargs)

    legacy_override.assert_called_once_with(**kwargs)
    assert result is legacy_override.return_value


def test_sync_session_hook_raises_when_no_session_contract_is_implemented() -> None:
    backend = _make_backend()

    with pytest.raises(NotImplementedError, match="Implement _get_sync_session or the legacy get_session method"):
        MemoryInterface._get_sync_session(backend)


def test_sync_session_hook_delegates_to_legacy_get_session_override() -> None:
    legacy_session = MagicMock(spec=Session)
    legacy_get_session = MagicMock(return_value=legacy_session)
    backend = _make_backend(overrides={"get_session": legacy_get_session})

    assert MemoryInterface._get_sync_session(backend) is legacy_session
    legacy_get_session.assert_called_once_with()


def test_base_async_engine_factory_requires_a_native_async_driver() -> None:
    backend = _make_backend()

    with pytest.raises(NotImplementedError, match="Implement _create_async_engine"):
        MemoryInterface._create_async_engine(backend)
