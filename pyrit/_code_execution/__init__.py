# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Experimental clients for caller-configured code-execution backends.

This private package is an explicit opt-in. Its API may change without a deprecation cycle.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit._code_execution.client import (
        SandboxBusyError,
        SandboxClient,
        SandboxExecutionError,
        SandboxInputError,
        SandboxRevisionChangedError,
        SandboxSessionLostError,
        SandboxUnavailableError,
    )
    from pyrit._code_execution.docker_client import DockerRuntimeProfile, DockerSandboxClient
    from pyrit._code_execution.fake_sandbox import FakeRun, FakeSandbox, FakeSandboxHook
    from pyrit._code_execution.sandbox import (
        ProcessResult,
        RuntimeProfile,
        Sandbox,
        SandboxCapabilities,
        SandboxCondition,
        SandboxResetRecord,
        SandboxStateKind,
        SandboxStatus,
        SandboxView,
    )
    from pyrit._code_execution.sandbox_contract import SandboxContractError, SandboxProbes, check_sandbox_contract_async

_LAZY_EXPORTS: dict[str, str] = {
    "SandboxClient": "pyrit._code_execution.client",
    "SandboxExecutionError": "pyrit._code_execution.client",
    "SandboxInputError": "pyrit._code_execution.client",
    "SandboxBusyError": "pyrit._code_execution.client",
    "SandboxSessionLostError": "pyrit._code_execution.client",
    "SandboxRevisionChangedError": "pyrit._code_execution.client",
    "SandboxUnavailableError": "pyrit._code_execution.client",
    "DockerRuntimeProfile": "pyrit._code_execution.docker_client",
    "DockerSandboxClient": "pyrit._code_execution.docker_client",
    "FakeRun": "pyrit._code_execution.fake_sandbox",
    "FakeSandbox": "pyrit._code_execution.fake_sandbox",
    "FakeSandboxHook": "pyrit._code_execution.fake_sandbox",
    "ProcessResult": "pyrit._code_execution.sandbox",
    "RuntimeProfile": "pyrit._code_execution.sandbox",
    "Sandbox": "pyrit._code_execution.sandbox",
    "SandboxCapabilities": "pyrit._code_execution.sandbox",
    "SandboxCondition": "pyrit._code_execution.sandbox",
    "SandboxResetRecord": "pyrit._code_execution.sandbox",
    "SandboxStateKind": "pyrit._code_execution.sandbox",
    "SandboxStatus": "pyrit._code_execution.sandbox",
    "SandboxView": "pyrit._code_execution.sandbox",
    "SandboxContractError": "pyrit._code_execution.sandbox_contract",
    "SandboxProbes": "pyrit._code_execution.sandbox_contract",
    "check_sandbox_contract_async": "pyrit._code_execution.sandbox_contract",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
