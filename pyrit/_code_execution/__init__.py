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
    from pyrit._code_execution.client import SandboxClient, SandboxExecutionError
    from pyrit._code_execution.docker_client import DockerRuntimeProfile, DockerSandboxClient

_LAZY_EXPORTS: dict[str, str] = {
    "SandboxClient": "pyrit._code_execution.client",
    "SandboxExecutionError": "pyrit._code_execution.client",
    "DockerRuntimeProfile": "pyrit._code_execution.docker_client",
    "DockerSandboxClient": "pyrit._code_execution.docker_client",
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
