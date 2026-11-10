# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""An in-memory ``Sandbox`` for tests and examples. It never runs code."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from pyrit._code_execution.client import SandboxRevisionChangedError, SandboxSessionLostError
from pyrit._code_execution.sandbox import (
    ProcessResult,
    RuntimeProfile,
    Sandbox,
    SandboxCapabilities,
    SandboxCondition,
    SandboxStateKind,
    SandboxStatus,
)
from pyrit.models import ComponentIdentifier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True)
class FakeRun:
    """One run a ``FakeSandbox`` was asked to do."""

    session_id: str | None
    runtime_profile_name: str
    program: str
    arguments: tuple[str, ...]
    stdin: str | None
    timeout_seconds: float


class FakeSandboxHook(str, Enum):
    """The adapter hook a ``FakeSandbox`` fault applies to."""

    OPEN = "open"
    EXECUTE = "execute"
    CLOSE = "close"
    STATUS = "status"
    RESET = "reset"


class FakeSandbox(Sandbox):
    """
    An in-memory sandbox for tests and examples. It never runs code.

    A handler decides what each run returns; by default every run exits 0 with no output. The fake keeps
    real session rules (a run in a closed or expired session is lost), a ``revision`` that resets check,
    a ``state`` dictionary that handlers may use and resets clear, and a record of every run.
    ``fail_next`` makes a hook raise, and ``gate`` holds runs until a test opens it.
    """

    DEFAULT_CAPABILITIES: ClassVar[SandboxCapabilities] = SandboxCapabilities(
        runtime_profiles=(
            RuntimeProfile(name="python", description="Python 3"),
            RuntimeProfile(name="bash", description="bash"),
        ),
        state_kind=SandboxStateKind.ENVIRONMENT,
        description="a fake sandbox that runs nothing",
        supports_reset=True,
        file_runtime_profile_name="python",
    )

    def __init__(
        self,
        *,
        name: str = "fake",
        capabilities: SandboxCapabilities | None = None,
        handler: Callable[[FakeRun], ProcessResult | Awaitable[ProcessResult]] | None = None,
        acquire_timeout_seconds: float = Sandbox.DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = Sandbox.DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initialize the fake.

        Args:
            name (str): Distinguishes fakes in identifiers and registries. Defaults to ``"fake"``.
            capabilities (SandboxCapabilities | None): What the fake offers. Defaults to
                ``DEFAULT_CAPABILITIES``: Python and bash in a resettable ``environment`` sandbox.
            handler (Callable[[FakeRun], ProcessResult | Awaitable[ProcessResult]] | None): Decides each run's
                result. Defaults to exit code 0 with no output.
            acquire_timeout_seconds (float): Passed to ``Sandbox``.
            idle_timeout_seconds (float): Passed to ``Sandbox``.
        """
        super().__init__(acquire_timeout_seconds=acquire_timeout_seconds, idle_timeout_seconds=idle_timeout_seconds)
        self._name = name
        self._fake_capabilities = capabilities or self.DEFAULT_CAPABILITIES
        self._handler = handler or (lambda run: ProcessResult(exit_code=0))
        self._faults: dict[FakeSandboxHook, list[BaseException]] = {hook: [] for hook in FakeSandboxHook}
        self._session_count = 0
        self.runs: list[FakeRun] = []
        self.state: dict[str, object] = {}
        self.revision = "fake-revision-1"
        self.condition = SandboxCondition.READY
        self.open_sessions: set[str] = set()
        self.closed_sessions: list[str] = []
        self.reset_count = 0
        self.gate: asyncio.Event | None = None
        self.run_started = asyncio.Event()

    def fail_next(self, *, hook: FakeSandboxHook, error: BaseException, times: int = 1) -> None:
        """
        Make the next calls to a hook raise an error.

        Args:
            hook (FakeSandboxHook): The hook that fails.
            error (BaseException): The error it raises.
            times (int): How many calls fail. Defaults to 1.
        """
        self._faults[hook].extend([error] * times)

    def expire_sessions(self) -> None:
        """End every open session, as an infrastructure that expires sessions would."""
        self.open_sessions.clear()
        if self._fake_capabilities.state_kind is SandboxStateKind.SESSION:
            self.state.clear()

    def _build_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(
            self, params={"name": self._name, "state_kind": self._fake_capabilities.state_kind.value}
        )

    def _get_capabilities(self) -> SandboxCapabilities:
        return self._fake_capabilities

    async def _execute_async(
        self,
        *,
        session_id: str | None,
        runtime_profile_name: str,
        program: str,
        arguments: tuple[str, ...],
        stdin: str | None,
        timeout_seconds: float,
    ) -> ProcessResult:
        self._raise_fault(FakeSandboxHook.EXECUTE)
        if self._fake_capabilities.state_kind is not SandboxStateKind.CALL and session_id not in self.open_sessions:
            raise SandboxSessionLostError(f"Session {session_id} is not open; nothing ran.")
        run = FakeRun(
            session_id=session_id,
            runtime_profile_name=runtime_profile_name,
            program=program,
            arguments=arguments,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )
        self.runs.append(run)
        self.run_started.set()
        if self.gate is not None:
            await self.gate.wait()
        result = self._handler(run)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ProcessResult):
            raise TypeError(f"A FakeSandbox handler must return a ProcessResult, not {type(result).__name__}.")
        return result

    async def _open_session_async(self, *, owner: str) -> str:
        self._raise_fault(FakeSandboxHook.OPEN)
        self._session_count += 1
        session_id = f"fake-session-{self._session_count}"
        self.open_sessions.add(session_id)
        return session_id

    async def _close_session_async(self, *, session_id: str) -> None:
        self._raise_fault(FakeSandboxHook.CLOSE)
        if session_id in self.open_sessions and self._fake_capabilities.state_kind is SandboxStateKind.SESSION:
            self.state.clear()
        self.open_sessions.discard(session_id)
        self.closed_sessions.append(session_id)

    async def _get_status_async(self) -> SandboxStatus:
        self._raise_fault(FakeSandboxHook.STATUS)
        return SandboxStatus(condition=self.condition, revision=self.revision, details={"fake_sandbox": self._name})

    async def _reset_async(self, *, expected_revision: str | None) -> None:
        self._raise_fault(FakeSandboxHook.RESET)
        if expected_revision is not None and expected_revision != self.revision:
            raise SandboxRevisionChangedError(f"The revision is {self.revision}, not {expected_revision}.")
        self.state.clear()
        self.reset_count += 1

    def _raise_fault(self, hook: FakeSandboxHook) -> None:
        """
        Raise the next fault queued for a hook, if any.

        Raises:
            BaseException: The queued fault.
        """
        if self._faults[hook]:
            raise self._faults[hook].pop(0)
