# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
The experimental ``Sandbox`` base class: one interface for any infrastructure that runs code.

An adapter subclasses ``Sandbox`` and implements a few hooks that translate PyRIT's calls into its
infrastructure's API. This class enforces the rules every sandbox follows, so callers get the same
behavior from any of them: sharing, waiting, retries, renewal, cancellation, idle close, reset, and
evidence.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pyrit._code_execution.client import (
    SandboxBusyError,
    SandboxExecutionError,
    SandboxInputError,
    SandboxRevisionChangedError,
    SandboxSessionLostError,
    SandboxStateLostError,
    SandboxUnavailableError,
)
from pyrit.models import Identifiable
from pyrit.models.code_execution import (
    _ProgramExecutionEvent,
    _ProgramInvocation,
    _RuntimeProfileName,
    _SandboxRun,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Coroutine, Sequence

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class SandboxStateKind(str, Enum):
    """
    Whether a sandbox keeps state between calls, which decides how PyRIT shares it.

    ``ENVIRONMENT`` state survives closing a session. ``SESSION`` state lasts only until that session
    ends, so PyRIT never silently closes or replaces it. Both serialize callers through one session.
    ``CALL`` retains nothing between calls and runs them concurrently without sessions.
    """

    ENVIRONMENT = "environment"
    SESSION = "session"
    CALL = "call"


class SandboxCondition(str, Enum):
    """Whether a sandbox can be used now: ready, unavailable, or quarantined until an operator acts."""

    READY = "ready"
    UNAVAILABLE = "unavailable"
    QUARANTINED = "quarantined"


class RuntimeProfile(BaseModel):
    """
    One trusted way a sandbox runs program text, such as Python or PowerShell.

    ``name`` is what callers pass; the adapter maps it to its own configuration. ``tool_name``
    optionally names the runtime for model tools (``run_<tool_name>``), such as ``powershell`` for a
    profile named ``windows-powershell``. ``description`` tells a model what the runtime is, such as
    "Python 3.12".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _RuntimeProfileName
    tool_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]{1,60}$")
    description: str = Field(default="", max_length=200)


class SandboxCapabilities(BaseModel):
    """
    What a sandbox offers, and its limits. They stay the same for the sandbox's lifetime.

    Limits apply to each call. ``max_input_bytes`` bounds the UTF-8 size of the program text, its
    standard input and its arguments together; ``max_arguments_bytes``, when set, also bounds the
    arguments alone, for infrastructures that pass them on a command line. ``max_output_bytes`` is the
    most output of each stream the sandbox returns before marking it truncated. Timeouts are in seconds.
    ``max_session_seconds``
    is set when the infrastructure ends sessions after a fixed time, so PyRIT can renew a session
    before a call could run past it. ``file_runtime_profile_name`` names a Python 3 runtime that file
    tools run in. ``description`` tells a model where its code runs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_profiles: tuple[RuntimeProfile, ...] = Field(min_length=1)
    state_kind: SandboxStateKind
    description: str = Field(default="an isolated sandbox", min_length=1, max_length=500)
    supports_reset: bool = False
    file_runtime_profile_name: _RuntimeProfileName | None = None
    max_input_bytes: int = Field(default=65_536, ge=1)
    max_arguments_bytes: int | None = Field(default=None, ge=1)
    max_output_bytes: int = Field(default=32_768, ge=1)
    max_timeout_seconds: float = Field(default=120.0, gt=0)
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    max_session_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_consistency(self) -> SandboxCapabilities:
        """
        Reject capabilities that contradict each other.

        Returns:
            SandboxCapabilities: The validated capabilities.

        Raises:
            ValueError: If names repeat, a default exceeds its limit, or a feature needs persistent state.
        """
        names = [profile.name for profile in self.runtime_profiles]
        if len(set(names)) != len(names):
            raise ValueError("Runtime profile names must be unique.")
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ValueError("default_timeout_seconds cannot exceed max_timeout_seconds.")
        if self.file_runtime_profile_name is not None and self.file_runtime_profile_name not in names:
            raise ValueError("file_runtime_profile_name must name one of the runtime profiles.")
        stateful = (self.file_runtime_profile_name, self.max_session_seconds, self.supports_reset or None)
        if self.state_kind is SandboxStateKind.CALL and any(value is not None for value in stateful):
            raise ValueError("File tools, session limits and reset need a stateful sandbox.")
        return self

    def get_runtime_profile(self, name: str) -> RuntimeProfile:
        """
        Return the runtime profile with this name.

        Args:
            name (str): A runtime profile name.

        Returns:
            RuntimeProfile: The matching profile.

        Raises:
            SandboxInputError: If no runtime profile has this name.
        """
        for profile in self.runtime_profiles:
            if profile.name == name:
                return profile
        available = sorted(profile.name for profile in self.runtime_profiles)
        raise SandboxInputError(f"Runtime profile {name!r} is not offered by this sandbox; available: {available}.")


class SandboxStatus(BaseModel):
    """
    What the infrastructure reports about a sandbox.

    ``revision`` names its current baseline or configuration, which a reset can pin. ``owner`` is who
    holds its session, when the infrastructure knows. ``details`` carries nonsecret identifiers, such as
    those that find the infrastructure's own records of the sandbox.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    condition: SandboxCondition = SandboxCondition.READY
    reason: str | None = None
    revision: str | None = None
    owner: str | None = None
    details: dict[str, str] = Field(default_factory=dict)


class ProcessResult(BaseModel):
    """
    What one program run did, as an adapter reports it.

    ``exit_code`` is None when the program did not exit on its own, for example because it timed out.
    Each stream is marked truncated when the sandbox cut it. ``duration_ms`` defaults to the time PyRIT
    measured. ``execution_id`` is the infrastructure's identifier for the run, if it has one.
    ``runtime_image_digest`` and ``resolved_command`` are optional provenance. ``warnings`` report
    problems that do not invalidate the result. ``cleanup_confirmed`` is False when the sandbox could not
    confirm that the run's processes or resources were removed; PyRIT then starts nothing beside them: no
    further invocation of the request, and in an ``environment`` sandbox no further call, since the sandbox
    is quarantined until it is released or reset.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    resource_limit_exceeded: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
    execution_id: str | None = Field(default=None, min_length=1)
    runtime_image_digest: str | None = Field(default=None, min_length=1)
    resolved_command: tuple[str, ...] | None = Field(default=None, min_length=1)
    warnings: tuple[str, ...] = ()
    cleanup_confirmed: bool = True

    @model_validator(mode="after")
    def _validate_exit_state(self) -> ProcessResult:
        """
        Keep the exit state consistent with how the run ended.

        Returns:
            ProcessResult: The validated result.

        Raises:
            ValueError: If a run that timed out also reports an exit code of its own.
        """
        if self.timed_out and self.exit_code is not None:
            raise ValueError("A timed-out run did not exit on its own and has no exit code.")
        return self


class SandboxResetRecord(BaseModel):
    """The latest reset: when it started and finished, and whether it succeeded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at: datetime
    finished_at: datetime | None = None
    succeeded: bool | None = None
    error: str | None = None


class SandboxView(BaseModel):
    """
    A sandbox as PyRIT sees it: the infrastructure's status plus PyRIT's own session state.

    ``status`` is the last status read when ``status_is_current`` is False, because a call held the
    sandbox. ``condition`` is ``QUARANTINED`` while PyRIT refuses calls because it could not confirm a
    session closed or a reset finished, with the reason in ``quarantine_reason``; otherwise it is the
    status's condition. ``closed`` is True once ``close_async`` has been called.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: SandboxStatus
    status_is_current: bool
    condition: SandboxCondition
    quarantine_reason: str | None = None
    closed: bool = False
    session_open: bool
    session_age_seconds: float | None = None
    idle_seconds: float | None = None
    busy: bool
    resetting: bool
    last_reset: SandboxResetRecord | None = None


_CALLER_GAVE_UP_REASON = "the caller gave up"


class _CallerGaveUpError(SandboxUnavailableError):
    """The caller gave up before a run started, so PyRIT never started it."""

    def __init__(self) -> None:
        super().__init__("The caller gave up before the run started; nothing ran.")


@dataclass
class _ExecutionRequest:
    program: str
    runtime_profile_name: str
    invocations: tuple[_ProgramInvocation, ...]
    timeout_seconds: float
    caller: asyncio.Future[Any] | None = None
    """What the caller awaits, in an ``environment`` sandbox. Cancelling the caller cancels it at once."""

    def abandoned(self) -> bool:
        """
        Whether the caller gave up, so no more work should start for it.

        Returns:
            bool: True if the caller stopped waiting.
        """
        return self.caller is not None and self.caller.cancelled()

    async def sleep_unless_abandoned_async(self, seconds: float) -> None:
        """Wait, waking early if the caller gives up."""
        if self.caller is None:
            await asyncio.sleep(seconds)
        else:
            await asyncio.wait({self.caller}, timeout=seconds)


def _log_abandoned_outcome(task: asyncio.Task[Any]) -> None:
    """Retrieve the outcome of work whose caller gave up, so a failure is logged, not lost."""
    if task.cancelled():
        return
    error = task.exception()
    if isinstance(error, _CallerGaveUpError):
        logger.info("A sandbox call did not run: %s", error)
    elif error is not None:
        logger.warning("A sandbox operation whose caller gave up failed: %s", error)
    elif isinstance(result := task.result(), _SandboxRun) and result.reasons:
        gave_up = all(reason.endswith(_CALLER_GAVE_UP_REASON) for reason in result.reasons)
        logger.log(
            logging.INFO if gave_up else logging.WARNING,
            "A sandbox call whose caller gave up stopped early: %s",
            "; ".join(result.reasons),
        )


def _cancellation_requested() -> bool:
    """
    Whether the current task was asked to stop, as opposed to an adapter raising ``CancelledError`` by itself.

    Like ``asyncio.timeout``, this reads the task's count of pending cancellations, so a task that swallowed
    an earlier cancellation without ``uncancel()`` still counts as cancelled.

    Returns:
        bool: True if the task itself is being cancelled.
    """
    task = asyncio.current_task()
    return task is None or task.cancelling() > 0


class Sandbox(Identifiable, ABC):
    """
    Base class for sandboxes: a subclass adapts one infrastructure, and this class enforces the rules.

    Adapters implement ``_build_identifier`` (static configuration only, never session ids or secrets),
    ``_get_capabilities`` and ``_execute_async``. Stateful sandboxes also implement
    ``_open_session_async`` and ``_close_session_async`` (closing twice must be safe), and may implement
    ``_get_status_async`` and ``_reset_async``. Adapters raise ``SandboxInputError`` for bad input,
    ``SandboxBusyError`` when work can't start right now, ``SandboxSessionLostError`` when the session is
    gone and nothing ran, and ``SandboxRevisionChangedError`` when a reset is refused. Any other failure,
    including a ``CancelledError`` a hook raises by itself, is uncertain: it is never retried and reaches
    callers as a ``SandboxExecutionError``.

    An ``environment`` sandbox has one shared session that callers take turns using. A lock admits one
    call at a time, first come first served, within one deadline for waiting and retries. The first call
    opens the session; it closes after ``idle_timeout_seconds`` without use and at ``release_async`` or
    ``close_async``, and it is renewed before ``max_session_seconds`` could cut a call short. Runs, session
    closes and resets happen in tasks this class owns: a caller that gives up returns at once, while the
    work finishes and holds the lock until it does. Runs that have not started when their caller gives up,
    such as one waiting out a busy sandbox, never start. A busy open is retried with backoff, a lost
    session is reopened and the call retried once, and after an uncertain failure the session is closed
    before the next call. A failed close is retried by ``release_async``. Uncertain run cleanup or reset
    requires a successful reset or explicit operator ``recover_async``; normal release does not lift that
    quarantine. ``close_async`` is final.

    A ``session`` sandbox also serializes calls, but closing loses its state. It has no automatic idle
    close or renewal: callers must explicitly release or reset it. A lost session raises
    ``SandboxStateLostError`` without replacement; uncertain execution also blocks further use until
    explicit recovery or reset. A session-scoped reset is attempted before closing, so refusal does not
    first discard the state it refused to reset.

    A ``call`` sandbox shares nothing between calls, so its calls run concurrently in the caller's task,
    and its adapter cleans up when a call is cancelled.

    Each sandbox should be represented by one ``Sandbox`` object, used from one event loop, in one process.
    """

    DEFAULT_ACQUIRE_TIMEOUT_SECONDS: ClassVar[float] = 300.0
    DEFAULT_IDLE_TIMEOUT_SECONDS: ClassVar[float] = 600.0
    _RENEWAL_MARGIN_SECONDS: ClassVar[float] = 60.0
    _EXECUTE_GRACE_SECONDS: ClassVar[float] = 60.0
    _HOOK_TIMEOUT_SECONDS: ClassVar[float] = 120.0
    _RESET_TIMEOUT_SECONDS: ClassVar[float] = 1_800.0
    _STATUS_LOCK_WAIT_SECONDS: ClassVar[float] = 1.0
    _BUSY_RETRY_INITIAL_SECONDS: ClassVar[float] = 1.0
    _BUSY_RETRY_MAX_SECONDS: ClassVar[float] = 15.0

    def __init__(
        self,
        *,
        acquire_timeout_seconds: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        owner: str | None = None,
    ) -> None:
        """
        Initialize the shared state that enforces the sandbox rules.

        Args:
            acquire_timeout_seconds (float): How long a call may wait for its turn and for a busy
                sandbox before its first invocation. Later invocations get their own acquisition budget.
                Defaults to 300 seconds.
            idle_timeout_seconds (float): How long an unused environment session stays open. Session-owned
                state is never evicted automatically. Defaults to 600 seconds.
            owner (str | None): The name sessions are opened under. Defaults to a random
                ``pyrit-`` name for this object.

        Raises:
            ValueError: If a timeout is not positive or the owner is empty.
        """
        if acquire_timeout_seconds <= 0 or idle_timeout_seconds <= 0:
            raise ValueError("acquire_timeout_seconds and idle_timeout_seconds must be positive.")
        if owner is not None and not owner.strip():
            raise ValueError("owner must not be empty.")
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._owner = owner or f"pyrit-{uuid.uuid4().hex[:12]}"
        self._capabilities: SandboxCapabilities | None = None
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._session_opened_at: float | None = None
        self._last_used_at: float | None = None
        self._uses = 0
        self._closed = False
        self._quarantine_reason: str | None = None
        self._operator_recovery_required = False
        self._unconfirmed_session_ids: list[str] = []
        self._resetting = False
        self._last_reset: SandboxResetRecord | None = None
        self._last_status: SandboxStatus | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- public API ----------------------------------------------------------------------------

    def get_capabilities(self) -> SandboxCapabilities:
        """
        Return what this sandbox offers and its limits.

        Returns:
            SandboxCapabilities: The adapter's capabilities, read once.

        Raises:
            TypeError: If the adapter declares a feature without implementing its hooks.
        """
        if self._capabilities is None:
            capabilities = self._get_capabilities()
            self._validate_hooks(capabilities=capabilities)
            self._capabilities = capabilities
        return self._capabilities

    def get_runtime_profile_names(self) -> frozenset[str]:
        """
        Return the runtime profile names this sandbox offers.

        Returns:
            frozenset[str]: Names that select the sandbox's runtimes.
        """
        return frozenset(profile.name for profile in self.get_capabilities().runtime_profiles)

    def validate_request(
        self,
        *,
        program: str,
        runtime_profile_name: str,
        invocations: Sequence[_ProgramInvocation],
        timeout_seconds: float | None = None,
    ) -> None:
        """
        Check a request against this sandbox's runtimes and limits, without running anything.

        Args:
            program (str): The program text.
            runtime_profile_name (str): The runtime to run it in.
            invocations (Sequence[_ProgramInvocation]): The arguments and input of each run.
            timeout_seconds (float | None): The time limit of each run. Defaults to the sandbox's default.

        Raises:
            SandboxInputError: If the request does not fit this sandbox.
        """
        capabilities = self.get_capabilities()
        capabilities.get_runtime_profile(runtime_profile_name)
        if not invocations:
            raise SandboxInputError("At least one program invocation is required.")
        program_bytes = self._utf8_size(program, field="program")
        for invocation in invocations:
            arguments_bytes = sum(self._utf8_size(argument, field="argument") for argument in invocation.arguments)
            if capabilities.max_arguments_bytes is not None and arguments_bytes > capabilities.max_arguments_bytes:
                raise SandboxInputError(
                    f"The arguments take {arguments_bytes} bytes; this sandbox accepts at most "
                    f"{capabilities.max_arguments_bytes}. Pass long data through standard input instead."
                )
            size = program_bytes + arguments_bytes + self._utf8_size(invocation.stdin or "", field="stdin")
            if size > capabilities.max_input_bytes:
                raise SandboxInputError(
                    f"The program, its input and its arguments take {size} bytes; "
                    f"this sandbox accepts at most {capabilities.max_input_bytes}."
                )
        self._resolve_timeout(timeout_seconds)

    async def execute_async(
        self,
        *,
        program: str,
        runtime_profile_name: _RuntimeProfileName,
        invocations: tuple[_ProgramInvocation, ...] | None = None,
        timeout_seconds: float | None = None,
    ) -> _SandboxRun:
        """
        Run each invocation of the program once, in order.

        In an ``environment`` sandbox the runs share the session, so they see what earlier calls left.

        Args:
            program (str): The program text, run verbatim.
            runtime_profile_name (_RuntimeProfileName): One of ``get_runtime_profile_names``.
            invocations (tuple[_ProgramInvocation, ...] | None): The arguments and input of each run.
                Defaults to one run with neither.
            timeout_seconds (float | None): The time limit of each run. Defaults to the sandbox's default.

        Returns:
            _SandboxRun: One event per run that happened. If the sandbox fails after some runs, the
            result holds those events and the reason it stopped.

        Raises:
            SandboxInputError: If the request does not fit this sandbox; nothing ran.
            SandboxUnavailableError: If the sandbox is closed or quarantined; nothing ran.
            SandboxExecutionError: If the sandbox could not run the first invocation.
        """
        invocations = invocations if invocations is not None else (_ProgramInvocation(),)
        self.validate_request(
            program=program,
            runtime_profile_name=runtime_profile_name,
            invocations=invocations,
            timeout_seconds=timeout_seconds,
        )
        request = _ExecutionRequest(
            program=program,
            runtime_profile_name=runtime_profile_name,
            invocations=tuple(invocations),
            timeout_seconds=self._resolve_timeout(timeout_seconds),
        )
        self._raise_if_unusable()
        if self.get_capabilities().state_kind is SandboxStateKind.CALL:
            return await self._run_invocations_async(request=request, deadline=None)
        deadline = time.monotonic() + self._acquire_timeout_seconds
        await self._acquire_lock_async(deadline=deadline)
        try:
            self._raise_if_unusable()
            task = self._spawn(self._run_locked_async(request=request, deadline=deadline))
        except BaseException:
            self._unlock()
            raise
        # Recorded before the run can start (the task yields first), so it starts nothing for a caller
        # that has given up: cancelling the caller cancels this shield at once.
        waiter = asyncio.shield(task)
        request.caller = waiter
        return await self._await_owned_async(task, waiter=waiter)

    async def get_status_async(self) -> SandboxView:
        """
        Return the sandbox's status and PyRIT's session state, without waiting for a running call.

        Returns:
            SandboxView: The current view. If a call holds the sandbox, the last status read is reused.
        """
        status = await self._read_status_without_waiting_async()
        if status is None:
            last = self._last_status or SandboxStatus(reason="The status has not been read yet.")
            return self._build_view(status=last, status_is_current=False)
        return self._build_view(status=status, status_is_current=True)

    async def release_async(self) -> None:
        """
        End the session if no call is running. Only environment-owned state survives release.

        Release first closes again every session whose close was not confirmed, which also works after
        ``close_async``; the quarantine stays while any of those closes still fails. Release never opens a
        session. Uncertain run cleanup, reset or lost session-state quarantine requires explicit operator
        recovery or a successful reset. Once started, release runs to the end even if the caller gives up.

        Raises:
            SandboxBusyError: If a call or reset holds the sandbox.
        """
        if self.get_capabilities().state_kind is SandboxStateKind.CALL:
            return
        if not await self._try_lock_async():
            raise SandboxBusyError("A call or reset holds the sandbox; release it after that finishes.")
        await self._await_owned_async(self._spawn(self._release_locked_async()))

    async def recover_async(self) -> None:
        """
        Acknowledge operator-confirmed recovery, close old sessions, and clear the quarantine.

        Call only after an operator has confirmed stray work stopped or restored the backend. Session-owned
        state may be gone; callers must reconstruct it explicitly. Unconfirmed session closes still prevent
        recovery, and a permanently closed sandbox cannot be reopened.

        Raises:
            SandboxBusyError: If a call or reset holds the sandbox.
            SandboxUnavailableError: If the sandbox is closed or a session close still cannot be confirmed.
        """
        if self._closed:
            raise SandboxUnavailableError("The sandbox is closed; recovery cannot reopen it.")
        if self.get_capabilities().state_kind is SandboxStateKind.CALL:
            return
        if not await self._try_lock_async():
            raise SandboxBusyError("A call or reset holds the sandbox; recover it after that finishes.")
        await self._await_owned_async(self._spawn(self._release_locked_async(recover=True)))

    async def reset_async(self, *, expected_revision: str | None = None) -> None:
        """
        Restore the sandbox's baseline, after the running call finishes.

        Environment sessions close first. Session-owned state is closed only after the reset succeeds.
        Once started, a reset runs to the end even if the caller gives up.
        A successful reset clears a quarantine; a session whose close is still unconfirmed is closed again
        at the next release or close.

        Args:
            expected_revision (str | None): The revision the caller read; the reset is refused if the
                sandbox's revision changed since. None resets whatever the current revision is.

        Raises:
            SandboxInputError: If this sandbox does not support reset.
            SandboxUnavailableError: If the sandbox is closed.
            SandboxBusyError: If a reset is already running, or the sandbox stayed busy too long.
            SandboxRevisionChangedError: If the revision is not the expected one.
            SandboxExecutionError: If the reset failed.
        """
        if not self.get_capabilities().supports_reset:
            raise SandboxInputError("This sandbox does not support reset.")
        if self._closed:
            raise SandboxUnavailableError("The sandbox is closed.")
        if self._resetting:
            raise SandboxBusyError("A reset of this sandbox is already running.")
        self._resetting = True
        try:
            await self._acquire_lock_async(deadline=time.monotonic() + self._acquire_timeout_seconds)
        except BaseException:
            self._resetting = False
            raise
        if self._closed:
            self._resetting = False
            self._unlock()
            raise SandboxUnavailableError("The sandbox was closed while the reset waited.")
        task = self._spawn(self._reset_locked_async(expected_revision=expected_revision))
        await self._await_owned_async(task)

    async def close_async(self) -> None:
        """
        Stop using the sandbox for good, and close its session once the running call finishes.

        Calls that are waiting or arrive later are refused; use ``release_async`` to end the session and
        keep using the sandbox. The sandbox's state is kept. If a call still holds the sandbox after the
        acquire timeout, the session is left to the idle close. This never raises an error.
        """
        self._closed = True
        self._cancel_idle_close()
        if self.get_capabilities().state_kind is SandboxStateKind.CALL:
            return
        if not await self._wait_for_lock_async(timeout_seconds=self._acquire_timeout_seconds):
            logger.warning("Sandbox %s stayed busy; its session closes when it is idle.", type(self).__name__)
            return
        await self._await_owned_async(self._spawn(self._close_locked_async()))

    # ---- adapter hooks -------------------------------------------------------------------------

    @abstractmethod
    def _get_capabilities(self) -> SandboxCapabilities:
        """
        Describe the sandbox's runtimes, state kind and limits. Called once.

        Returns:
            SandboxCapabilities: The sandbox's capabilities.
        """

    @abstractmethod
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
        """
        Run the program once and report what it did.

        Stop the processes the program started directly when the run ends. Report the program's own
        failures, timeouts and limits in the result; raise only when the sandbox itself failed.

        Args:
            session_id (str | None): The open session, or None in a ``call`` sandbox.
            runtime_profile_name (str): One of the sandbox's runtime profile names.
            program (str): The program text, run verbatim.
            arguments (tuple[str, ...]): The program's command-line arguments.
            stdin (str | None): The program's standard input, if any.
            timeout_seconds (float): The time limit of the run.

        Returns:
            ProcessResult: What the run did.
        """

    async def _open_session_async(self, *, owner: str) -> str:
        """
        Open a session on the sandbox. Required for ``environment`` sandboxes.

        Args:
            owner (str): The name to open the session under.

        Returns:
            str: The session's identifier.

        Raises:
            NotImplementedError: If the adapter has no sessions.
        """
        raise NotImplementedError(f"{type(self).__name__} does not open sessions.")

    async def _close_session_async(self, *, session_id: str) -> None:
        """
        Close a session, keeping the sandbox's state. Closing a closed session must succeed.

        Args:
            session_id (str): The session to close.

        Raises:
            NotImplementedError: If the adapter has no sessions.
        """
        raise NotImplementedError(f"{type(self).__name__} does not close sessions.")

    async def _get_status_async(self) -> SandboxStatus:
        """
        Report the sandbox's condition, revision and nonsecret details. Defaults to ready.

        Returns:
            SandboxStatus: The sandbox's status.
        """
        return SandboxStatus()

    async def _reset_async(self, *, expected_revision: str | None) -> None:
        """
        Restore the sandbox's baseline, and return when it is done. Required when reset is supported.

        Args:
            expected_revision (str | None): Refuse with ``SandboxRevisionChangedError`` unless the
                revision is this one. None resets whatever the current revision is.

        Raises:
            NotImplementedError: If the adapter can't reset.
        """
        raise NotImplementedError(f"{type(self).__name__} does not reset.")

    # ---- running calls -------------------------------------------------------------------------

    async def _run_locked_async(self, *, request: _ExecutionRequest, deadline: float) -> _SandboxRun:
        """
        Run a request that holds the lock, and release the lock when the adapter has returned.

        Returns:
            _SandboxRun: The request's runs.
        """
        try:
            # Yield first: with an eager task factory, this task would otherwise start before
            # execute_async records the caller, and could not see the caller give up.
            await asyncio.sleep(0)
            return await self._run_invocations_async(request=request, deadline=deadline)
        finally:
            self._last_used_at = time.monotonic()
            self._uses += 1
            self._unlock()

    async def _run_invocations_async(self, *, request: _ExecutionRequest, deadline: float | None) -> _SandboxRun:
        """
        Run each invocation in order, stopping at the first failure or unconfirmed cleanup, or when the
        caller has given up.

        Returns:
            _SandboxRun: The runs that happened, with any reasons, warnings and execution ids.

        Raises:
            SandboxInputError: If the first invocation was rejected.
            _CallerGaveUpError: If the caller gave up before the first invocation started.
            SandboxExecutionError: If the first invocation could not run.
        """
        events: list[_ProgramExecutionEvent] = []
        warnings: list[str] = []
        execution_ids: list[str] = []
        reasons: tuple[str, ...] = ()
        for index, invocation in enumerate(request.invocations):
            try:
                if request.abandoned():
                    raise _CallerGaveUpError
                if deadline is not None and index:
                    deadline = time.monotonic() + self._acquire_timeout_seconds
                result = await self._invoke_async(request=request, invocation=invocation, deadline=deadline)
                events.append(self._to_event(index=index, request=request, result=result))
            except _CallerGaveUpError:
                if not events:
                    raise
                reasons = (f"stopped after invocation {index - 1}: {_CALLER_GAVE_UP_REASON}",)
                break
            except (SandboxExecutionError, SandboxInputError) as error:
                if not events:
                    raise
                reasons = (f"sandbox error at invocation {index}: {error}",)
                break
            warnings.extend(result.warnings)
            if result.execution_id is not None:
                execution_ids.append(result.execution_id)
            if not result.cleanup_confirmed:
                detail = "; ".join(result.warnings) or "cleanup was not confirmed"
                if not result.warnings:
                    warnings.append(detail)
                if deadline is not None:
                    self._quarantine(reason=f"A run's processes may still be running: {detail}", requires_recovery=True)
                if index + 1 < len(request.invocations):
                    reasons = (f"stopped after invocation {index}: {detail}",)
                break
        return _SandboxRun(
            events=tuple(events), reasons=reasons, warnings=tuple(warnings), execution_ids=tuple(execution_ids)
        )

    async def _invoke_async(
        self, *, request: _ExecutionRequest, invocation: _ProgramInvocation, deadline: float | None
    ) -> ProcessResult:
        """
        Run one invocation, in the session when the sandbox has one.

        Returns:
            ProcessResult: What the run did.
        """
        if deadline is None:
            return await self._call_execute_async(session_id=None, request=request, invocation=invocation)
        return await self._invoke_in_session_async(request=request, invocation=invocation, deadline=deadline)

    async def _invoke_in_session_async(
        self, *, request: _ExecutionRequest, invocation: _ProgramInvocation, deadline: float
    ) -> ProcessResult:
        """
        Run one invocation in the shared session, reopening a lost session and retrying once.

        Returns:
            ProcessResult: What the run did.
        """
        try:
            return await self._attempt_in_session_async(request=request, invocation=invocation, deadline=deadline)
        except SandboxSessionLostError:
            logger.info("Sandbox %s lost its session before a run; reopening once.", type(self).__name__)
        return await self._attempt_in_session_async(request=request, invocation=invocation, deadline=deadline)

    async def _attempt_in_session_async(
        self, *, request: _ExecutionRequest, invocation: _ProgramInvocation, deadline: float
    ) -> ProcessResult:
        """
        Run one invocation in the open session, opening or renewing it first.

        Returns:
            ProcessResult: What the run did.

        Raises:
            SandboxInputError: If the adapter rejected the input; nothing ran.
            SandboxUnavailableError: If the sandbox is closed or quarantined; nothing ran.
            _CallerGaveUpError: If the caller gave up while the session opened; nothing ran.
            SandboxBusyError: If the sandbox could not start the run; nothing ran.
            SandboxSessionLostError: If the session was gone; it is forgotten and nothing ran.
            SandboxStateLostError: If losing the session also lost its state; replacement is not automatic.
            SandboxExecutionError: If the outcome is uncertain; the session is closed first.
        """
        session_id = await self._ensure_session_async(request=request, deadline=deadline)
        if request.abandoned():
            raise _CallerGaveUpError
        try:
            return await self._call_execute_async(session_id=session_id, request=request, invocation=invocation)
        except (SandboxInputError, SandboxBusyError):
            raise
        except SandboxSessionLostError as error:
            self._forget_session()
            if self.get_capabilities().state_kind is SandboxStateKind.SESSION:
                reason = "The session's state was lost; explicitly recover or reset before starting a new session."
                self._quarantine(reason=reason, requires_recovery=True)
                raise SandboxStateLostError(reason) from error
            raise
        except SandboxExecutionError:
            if self.get_capabilities().state_kind is SandboxStateKind.SESSION:
                self._quarantine(
                    reason="The run is uncertain and closing its session discards the state.",
                    requires_recovery=True,
                )
            await self._discard_session_async()
            raise

    async def _call_execute_async(
        self, *, session_id: str | None, request: _ExecutionRequest, invocation: _ProgramInvocation
    ) -> ProcessResult:
        """
        Call the adapter, bounded by the run's timeout plus a grace period, with failures classified.

        Returns:
            ProcessResult: What the run did, validated, with its duration filled in.

        Raises:
            SandboxInputError: If the adapter rejected the input.
            SandboxExecutionError: If the adapter failed, did not answer in time, or returned an invalid result.
            CancelledError: If this task is being cancelled.
        """
        started = time.monotonic()
        try:
            async with asyncio.timeout(request.timeout_seconds + self._EXECUTE_GRACE_SECONDS):
                result = await self._execute_async(
                    session_id=session_id,
                    runtime_profile_name=request.runtime_profile_name,
                    program=request.program,
                    arguments=tuple(invocation.arguments),
                    stdin=invocation.stdin,
                    timeout_seconds=request.timeout_seconds,
                )
            # Validate again: model_copy and model_construct skip validation.
            result = ProcessResult.model_validate(result.model_dump())
        except (SandboxExecutionError, SandboxInputError):
            raise
        except TimeoutError as error:
            raise SandboxExecutionError("The sandbox did not answer in time; the outcome is uncertain.") from error
        except asyncio.CancelledError as error:
            if _cancellation_requested():
                raise
            raise SandboxExecutionError("The sandbox cancelled the run by itself; the outcome is uncertain.") from error
        except Exception as error:
            message = (
                f"The sandbox failed during the run, or returned an invalid result; the outcome is uncertain: {error}"
            )
            raise SandboxExecutionError(message) from error
        if result.duration_ms is None:
            result = result.model_copy(update={"duration_ms": int((time.monotonic() - started) * 1000)})
        return result

    def _to_event(self, *, index: int, request: _ExecutionRequest, result: ProcessResult) -> _ProgramExecutionEvent:
        """
        Record an adapter's result as an execution event.

        Returns:
            _ProgramExecutionEvent: The event.

        Raises:
            SandboxExecutionError: If the result can't be recorded.
        """
        try:
            return _ProgramExecutionEvent(
                invocation_index=index,
                runtime_profile_name=request.runtime_profile_name,
                runtime_image_digest=result.runtime_image_digest,
                resolved_command=result.resolved_command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
                timed_out=result.timed_out,
                resource_limit_exceeded=result.resource_limit_exceeded,
                duration_ms=result.duration_ms or 0,
            )
        except ValidationError as error:
            raise SandboxExecutionError(f"The sandbox returned a result that can't be recorded: {error}") from error

    # ---- sessions ------------------------------------------------------------------------------

    async def _ensure_session_async(self, *, request: _ExecutionRequest, deadline: float) -> str:
        """
        Return the open session, renewing it first if the request's run could outlast it.

        Returns:
            str: The session identifier.

        Raises:
            SandboxBusyError: If opening exhausted the acquisition budget or the session's usable lifetime.
            SandboxUnavailableError: If renewal would discard session-owned state.
        """
        if self._session_id is not None and not self._needs_renewal(timeout_seconds=request.timeout_seconds):
            return self._session_id
        if self._session_id is not None:
            if self.get_capabilities().state_kind is SandboxStateKind.SESSION:
                raise SandboxUnavailableError(
                    "This run cannot fit the session's remaining lifetime without losing its state; "
                    "use a shorter timeout or explicitly release or reset it."
                )
            await self._discard_session_async()
        session_id = await self._open_session_with_retry_async(request=request, deadline=deadline)
        if time.monotonic() >= deadline:
            reason = "The sandbox acquisition deadline expired while opening a session; nothing ran."
        elif self._needs_renewal(timeout_seconds=request.timeout_seconds):
            reason = "The opened session has insufficient lifetime for this run; nothing ran."
        else:
            return session_id
        await self._discard_session_async()
        self._raise_if_unusable()
        raise SandboxBusyError(reason)

    def _needs_renewal(self, *, timeout_seconds: float) -> bool:
        """
        Whether the session could end before a run of this length finishes.

        Returns:
            bool: True if the session should be reopened first.
        """
        limit = self.get_capabilities().max_session_seconds
        if limit is None or self._session_opened_at is None:
            return False
        remaining = self._session_opened_at + limit - time.monotonic()
        return remaining < timeout_seconds + self._RENEWAL_MARGIN_SECONDS

    async def _open_session_with_retry_async(self, *, request: _ExecutionRequest, deadline: float) -> str:
        """
        Open a session for a request, retrying a busy sandbox with backoff until the deadline.

        Returns:
            str: The new session's identifier.

        Raises:
            SandboxUnavailableError: If the sandbox is closed or quarantined.
            _CallerGaveUpError: If the caller gave up.
            SandboxBusyError: If the sandbox is still busy at the deadline.
            SandboxExecutionError: If the open failed.
        """
        delay = self._BUSY_RETRY_INITIAL_SECONDS
        while True:
            self._raise_if_unusable()
            if request.abandoned():
                raise _CallerGaveUpError
            sent_at = time.monotonic()
            if sent_at >= deadline:
                raise SandboxBusyError(
                    "The sandbox acquisition deadline expired before opening a session; nothing ran."
                )
            try:
                session_id = await self._call_control_async(
                    self._open_session_async(owner=self._owner), action="open a session"
                )
            except SandboxBusyError:
                if time.monotonic() + delay > deadline:
                    raise
                await request.sleep_unless_abandoned_async(delay)
                delay = min(delay * 2, self._BUSY_RETRY_MAX_SECONDS)
                continue
            self._session_id, self._session_opened_at = session_id, sent_at
            return session_id

    async def _discard_session_async(self) -> None:
        """Close the session and forget it; quarantine the sandbox if the close can't be confirmed."""
        session_id = self._session_id
        self._forget_session()
        if session_id is not None:
            await self._close_or_quarantine_async(session_id=session_id)

    async def _close_or_quarantine_async(self, *, session_id: str) -> bool:
        """
        Close a session. Until a close is confirmed, the session stays listed as unconfirmed, and a close
        that fails quarantines the sandbox.

        Returns:
            bool: Whether the session is known to be closed.
        """
        if session_id not in self._unconfirmed_session_ids:
            self._unconfirmed_session_ids.append(session_id)
        try:
            await self._call_control_async(self._close_session_async(session_id=session_id), action="close a session")
        except SandboxSessionLostError:
            pass
        except SandboxExecutionError as error:
            self._quarantine(reason=f"Closing session {session_id} could not be confirmed: {error}")
            return False
        self._unconfirmed_session_ids.remove(session_id)
        return True

    async def _retry_unconfirmed_closes_async(self) -> bool:
        """
        Close again each session whose close was not confirmed.

        Returns:
            bool: Whether every such session is now known to be closed.
        """
        for session_id in list(self._unconfirmed_session_ids):
            await self._close_or_quarantine_async(session_id=session_id)
        return not self._unconfirmed_session_ids

    def _quarantine(self, *, reason: str, requires_recovery: bool = False) -> None:
        """Refuse calls, retaining whether ordinary confirmed-close recovery is insufficient."""
        self._quarantine_reason = reason
        self._operator_recovery_required |= requires_recovery
        logger.warning("Sandbox %s is quarantined: %s", type(self).__name__, reason)

    def _forget_session(self) -> None:
        """Drop the session without closing it, when it is already gone."""
        self._session_id = None
        self._session_opened_at = None
        self._cancel_idle_close()

    async def _release_locked_async(self, *, recover: bool = False) -> None:
        """
        End the session and retry unconfirmed closes, clearing only a justified quarantine.

        Raises:
            SandboxUnavailableError: If explicit recovery still has an unconfirmed close.
        """
        try:
            await self._retry_unconfirmed_closes_async()
            await self._discard_session_async()
            if not self._unconfirmed_session_ids and (recover or not self._operator_recovery_required):
                self._quarantine_reason = None
                self._operator_recovery_required = False
            if recover and self._quarantine_reason is not None:
                raise SandboxUnavailableError(f"The sandbox is still quarantined: {self._quarantine_reason}")
        finally:
            self._unlock()

    async def _close_locked_async(self) -> None:
        """Retry each unconfirmed close and end the session while holding the lock, then release it."""
        try:
            await self._retry_unconfirmed_closes_async()
            await self._discard_session_async()
            if not self._unconfirmed_session_ids and not self._operator_recovery_required:
                self._quarantine_reason = None
        finally:
            self._lock.release()

    # ---- status, reset and idle close ----------------------------------------------------------

    async def _read_status_without_waiting_async(self) -> SandboxStatus | None:
        """
        Read the status unless a call holds the sandbox.

        Returns:
            SandboxStatus | None: The status, or None if the sandbox is held.
        """
        if self.get_capabilities().state_kind is SandboxStateKind.CALL:
            return await self._read_status_async()
        if not await self._try_lock_async():
            return None
        try:
            return await self._read_status_async()
        finally:
            self._unlock()

    async def _read_status_async(self) -> SandboxStatus:
        """
        Call the adapter for its status, reporting a failure as unavailable.

        Returns:
            SandboxStatus: The status, which is also kept as the last one read.
        """
        try:
            status = await self._call_control_async(self._get_status_async(), action="read the status")
        except SandboxExecutionError as error:
            status = SandboxStatus(condition=SandboxCondition.UNAVAILABLE, reason=str(error))
        self._last_status = status
        return status

    def _build_view(self, *, status: SandboxStatus, status_is_current: bool) -> SandboxView:
        """
        Combine a status with PyRIT's own session state.

        Returns:
            SandboxView: The view.
        """
        now = time.monotonic()
        session_open = self._session_id is not None
        return SandboxView(
            status=status,
            status_is_current=status_is_current,
            condition=SandboxCondition.QUARANTINED if self._quarantine_reason else status.condition,
            quarantine_reason=self._quarantine_reason,
            closed=self._closed,
            session_open=session_open,
            session_age_seconds=now - self._session_opened_at if self._session_opened_at is not None else None,
            idle_seconds=now - self._last_used_at if session_open and self._last_used_at is not None else None,
            busy=self._lock.locked(),
            resetting=self._resetting,
            last_reset=self._last_reset,
        )

    async def _reset_locked_async(self, *, expected_revision: str | None) -> None:
        """
        Close the session and reset while holding the lock, recording the outcome.

        A reset that was refused changed nothing. Any other failure leaves the sandbox in an unknown state,
        so it is quarantined.

        Raises:
            SandboxExecutionError: If the reset failed or was refused.
        """
        started_at = datetime.now(UTC)
        self._last_reset = SandboxResetRecord(started_at=started_at)
        try:
            session_owned = self.get_capabilities().state_kind is SandboxStateKind.SESSION
            if session_owned:
                await self._call_reset_async(expected_revision=expected_revision)
            await self._retry_unconfirmed_closes_async()
            await self._discard_session_async()
            if not session_owned:
                await self._call_reset_async(expected_revision=expected_revision)
        except Exception as error:
            if not isinstance(error, (SandboxInputError, SandboxBusyError, SandboxRevisionChangedError)):
                self._quarantine(reason=f"The reset did not finish: {error}", requires_recovery=True)
            self._last_reset = SandboxResetRecord(
                started_at=started_at, finished_at=datetime.now(UTC), succeeded=False, error=str(error)
            )
            raise
        else:
            self._quarantine_reason = None
            self._operator_recovery_required = False
            if self._unconfirmed_session_ids:
                logger.warning(
                    "Sandbox %s was reset, but closing sessions %s is unconfirmed; release and close retry it.",
                    type(self).__name__,
                    ", ".join(self._unconfirmed_session_ids),
                )
            self._last_reset = SandboxResetRecord(started_at=started_at, finished_at=datetime.now(UTC), succeeded=True)
        finally:
            self._resetting = False
            self._last_status = None
            self._unlock()

    async def _call_reset_async(self, *, expected_revision: str | None) -> None:
        """
        Call the adapter's reset, bounded in time, with failures classified.

        Raises:
            SandboxInputError: If the adapter rejected the request.
            SandboxExecutionError: If the reset failed or did not finish in time.
            CancelledError: If this task is being cancelled.
        """
        try:
            async with asyncio.timeout(self._RESET_TIMEOUT_SECONDS):
                await self._reset_async(expected_revision=expected_revision)
        except (SandboxExecutionError, SandboxInputError):
            raise
        except TimeoutError as error:
            raise SandboxExecutionError(f"The reset did not finish in {self._RESET_TIMEOUT_SECONDS:g} s.") from error
        except asyncio.CancelledError as error:
            if _cancellation_requested():
                raise
            raise SandboxExecutionError("The reset failed: the sandbox cancelled it by itself.") from error
        except Exception as error:
            raise SandboxExecutionError(f"The reset failed: {error}") from error

    def _schedule_idle_close(self) -> None:
        """Close the session once it has been idle long enough."""
        self._cancel_idle_close()
        if self._session_id is None or self._last_used_at is None:
            return
        if self.get_capabilities().state_kind is SandboxStateKind.SESSION and not self._closed:
            return
        delay = 0.0 if self._closed else max(self._last_used_at + self._idle_timeout_seconds - time.monotonic(), 0.0)
        self._idle_handle = asyncio.get_running_loop().call_later(delay, self._on_idle_timer, self._uses)

    def _cancel_idle_close(self) -> None:
        """Cancel a scheduled idle close."""
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    def _on_idle_timer(self, uses: int) -> None:
        """Start closing an idle session, unless something holds the sandbox; its holder reschedules."""
        self._idle_handle = None
        if self._session_id is not None and not self._lock.locked():
            self._spawn(self._close_if_idle_async(uses=uses))

    async def _close_if_idle_async(self, *, uses: int) -> None:
        """
        Close the session if no call has used it since the idle timer was set.

        Counting uses rather than reading the clock keeps this correct when a timer fires a clock tick early
        and when two uses fall in one tick. A call that used the session in the meantime rescheduled the
        timer when it released the lock.
        """
        async with self._lock:
            if self._session_id is not None and self._uses == uses:
                await self._discard_session_async()

    # ---- helpers -------------------------------------------------------------------------------

    def _raise_if_unusable(self) -> None:
        """
        Refuse new work, including a new session, once the sandbox is closed or while it is quarantined.

        Raises:
            SandboxUnavailableError: If the sandbox is closed or quarantined; nothing ran.
        """
        if self._closed:
            raise SandboxUnavailableError("The sandbox is closed; no new calls start.")
        if self._quarantine_reason is not None:
            raise SandboxUnavailableError(
                f"The sandbox is quarantined until recovery completes: {self._quarantine_reason}"
            )

    def _resolve_timeout(self, timeout_seconds: float | None) -> float:
        """
        Apply the default timeout and check the limits.

        Returns:
            float: The timeout to use.

        Raises:
            SandboxInputError: If the timeout is out of range or can't fit in a session.
        """
        capabilities = self.get_capabilities()
        timeout = capabilities.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not 0 < timeout <= capabilities.max_timeout_seconds:
            raise SandboxInputError(
                f"timeout_seconds must be more than 0 and at most {capabilities.max_timeout_seconds:g}."
            )
        limit = capabilities.max_session_seconds
        if limit is not None and timeout + self._RENEWAL_MARGIN_SECONDS > limit:
            raise SandboxInputError(
                f"timeout_seconds must be at most {limit - self._RENEWAL_MARGIN_SECONDS:g} for this sandbox's "
                "session limit."
            )
        return float(timeout)

    @staticmethod
    def _utf8_size(text: str, *, field: str) -> int:
        """
        Return the UTF-8 size of a text.

        Returns:
            int: The size in bytes.

        Raises:
            SandboxInputError: If the text can't be encoded as UTF-8.
        """
        try:
            return len(text.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise SandboxInputError(f"The {field} is not valid UTF-8 text.") from error

    async def _acquire_lock_async(self, *, deadline: float) -> None:
        """
        Wait for the sandbox's turn, up to the deadline. Nothing has started if this raises.

        Raises:
            SandboxBusyError: If the deadline passed first.
        """
        if not await self._wait_for_lock_async(timeout_seconds=deadline - time.monotonic()):
            raise SandboxBusyError(f"The sandbox stayed busy for {self._acquire_timeout_seconds:g} s; nothing ran.")

    async def _try_lock_async(self) -> bool:
        """
        Take the lock if nothing holds it, without waiting for a call.

        Returns:
            bool: Whether the lock is now held.
        """
        if self._lock.locked():
            return False
        return await self._wait_for_lock_async(timeout_seconds=self._STATUS_LOCK_WAIT_SECONDS)

    async def _wait_for_lock_async(self, *, timeout_seconds: float) -> bool:
        """
        Wait up to a time limit for the lock.

        ``asyncio.timeout`` rather than ``asyncio.wait_for``: on Python 3.11, ``wait_for`` can drop a
        caller's cancellation that arrives as the lock is handed over, so a caller that gave up would run.

        Returns:
            bool: Whether the lock is now held.
        """
        try:
            async with asyncio.timeout(max(timeout_seconds, 0.0)):
                await self._lock.acquire()
        except TimeoutError:
            return False
        return True

    def _unlock(self) -> None:
        """Release the lock and reschedule the idle close, which skips a held sandbox."""
        self._lock.release()
        self._schedule_idle_close()

    async def _call_control_async(self, work: Awaitable[_T], *, action: str) -> _T:
        """
        Await an adapter's session or status call, bounded in time, with failures classified.

        Returns:
            _T: The call's result.

        Raises:
            SandboxExecutionError: If the call failed or did not answer in time.
            CancelledError: If this task is being cancelled.
        """
        try:
            async with asyncio.timeout(self._HOOK_TIMEOUT_SECONDS):
                return await work
        except SandboxExecutionError:
            raise
        except TimeoutError as error:
            raise SandboxExecutionError(f"Could not {action}: no answer in time.") from error
        except asyncio.CancelledError as error:
            if _cancellation_requested():
                raise
            raise SandboxExecutionError(f"Could not {action}: the sandbox cancelled the call by itself.") from error
        except Exception as error:
            raise SandboxExecutionError(f"Could not {action}: {error}") from error

    def _spawn(self, work: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        """
        Start work this sandbox owns, keeping a reference until it finishes.

        Returns:
            asyncio.Task[_T]: The started task.
        """
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @staticmethod
    async def _await_owned_async(task: asyncio.Task[_T], *, waiter: asyncio.Future[_T] | None = None) -> _T:
        """
        Wait for owned work; a caller that gives up returns at once, and the work finishes alone.

        Args:
            task (asyncio.Task[_T]): The owned work.
            waiter (asyncio.Future[_T] | None): The shield over ``task`` to wait on, when the work watches
                it to see whether its caller gave up. Defaults to a new shield.

        Returns:
            _T: The work's result.

        Raises:
            CancelledError: If the caller was cancelled; the work continues.
        """
        try:
            return await (waiter if waiter is not None else asyncio.shield(task))
        except asyncio.CancelledError:
            # Even when the work finished at the same moment: the shield marked its error as retrieved.
            task.add_done_callback(_log_abandoned_outcome)
            raise

    def _validate_hooks(self, *, capabilities: SandboxCapabilities) -> None:
        """
        Check that the adapter implements the hooks its capabilities need.

        Raises:
            TypeError: If a needed hook is missing.
        """
        needed = []
        if capabilities.state_kind is not SandboxStateKind.CALL:
            needed += ["_open_session_async", "_close_session_async"]
        if capabilities.supports_reset:
            needed.append("_reset_async")
        sandbox_type = type(self)
        missing = [hook for hook in needed if getattr(sandbox_type, hook) is getattr(Sandbox, hook)]
        if missing:
            raise TypeError(f"{sandbox_type.__name__} must implement {', '.join(missing)} for its capabilities.")
