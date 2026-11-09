# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyrit._code_execution import FakeSandbox, SandboxRegistry, resolve_sandbox
from pyrit.setup.initialization import reset_setup_registries

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def registry() -> Iterator[SandboxRegistry]:
    SandboxRegistry.reset_registry_singleton()
    yield SandboxRegistry.get_registry_singleton()
    SandboxRegistry.reset_registry_singleton()


def test_register_and_find_by_name(registry: SandboxRegistry) -> None:
    sandbox = FakeSandbox(name="linux-1")
    registry.register(sandbox, name="linux-1")
    assert registry.get("linux-1") is sandbox
    assert registry.get_names() == ["linux-1"]
    assert resolve_sandbox("linux-1") is sandbox
    assert resolve_sandbox(sandbox) is sandbox
    assert registry.unregister("linux-1") is sandbox
    assert registry.get("linux-1") is None


@pytest.mark.parametrize("name", ["", "has space", "../escape", "-leading", "x" * 129])
def test_register_rejects_invalid_names(registry: SandboxRegistry, name: str) -> None:
    with pytest.raises(ValueError, match="Sandbox name"):
        registry.register(FakeSandbox(), name=name)


def test_register_rejects_a_taken_name(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(name="one"), name="sandbox")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeSandbox(name="two"), name="sandbox")


def test_register_rejects_a_second_object_for_the_same_sandbox(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(name="windows"), name="windows-1")
    with pytest.raises(ValueError, match="already registered as 'windows-1'"):
        registry.register(FakeSandbox(name="windows"), name="windows-2")


def test_resolve_names_what_is_registered(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(), name="present")
    with pytest.raises(ValueError, match=r"No sandbox is registered as 'absent'; registered: \['present'\]"):
        resolve_sandbox("absent")


async def test_close_all_closes_every_session(registry: SandboxRegistry) -> None:
    sandboxes = [FakeSandbox(name=name) for name in ("a", "b")]
    for sandbox in sandboxes:
        registry.register(sandbox, name=sandbox.get_identifier().params["name"])
        await sandbox.execute_async(program="print(1)", runtime_profile_name="python")
    await registry.close_all_async()
    assert all(sandbox.open_sessions == set() for sandbox in sandboxes)


def test_reapplying_configuration_resets_the_registry(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(), name="before")
    reset_setup_registries()
    assert SandboxRegistry.get_registry_singleton().get_names() == []
