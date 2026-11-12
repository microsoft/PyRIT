# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire fixed-check evidence from caller-owned state, without managing that state's lifetime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit._code_execution import SandboxStateKind
from pyrit.score.observation.program_execution_source import _ProgramExecutionSource

if TYPE_CHECKING:
    from pyrit._code_execution import Sandbox


class _SandboxCheckSource(_ProgramExecutionSource):
    """
    Run check invocations against the state retained by the exact sandbox shared with tools.

    ``SESSION`` state lasts until its session ends; ``ENVIRONMENT`` state survives a session close.
    Acquisition runs anew without resetting, releasing or recovering either kind. Sandbox owns locking,
    retries and state-loss quarantine; the caller owns attempt isolation and explicit lifecycle actions.
    """

    def __init__(self, *, sandbox: Sandbox, max_output_chars: int = 64_000) -> None:
        """
        Initialize with an explicitly stateful sandbox.

        Args:
            sandbox (Sandbox): The same stateful object used by the model's tools.
            max_output_chars (int): The most characters of each stream retained per invocation.

        Raises:
            ValueError: If the output limit is not positive or the sandbox retains no state.
        """
        super().__init__(sandbox=sandbox, max_output_chars=max_output_chars)
        if sandbox.get_capabilities().state_kind not in (SandboxStateKind.ENVIRONMENT, SandboxStateKind.SESSION):
            raise ValueError("A check needs a sandbox whose state persists, so it can see what the model's code did.")
