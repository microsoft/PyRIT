# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
A contract-test kit for ``Sandbox`` adapters.

Run ``check_sandbox_contract_async`` against a live sandbox, from your adapter's own tests, to check the
rules PyRIT relies on: declared limits, error mapping, state, status, reset, closing twice, and a
static identifier.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit._code_execution.client import (
    SandboxBusyError,
    SandboxExecutionError,
    SandboxInputError,
    SandboxRevisionChangedError,
    SandboxSessionLostError,
)
from pyrit._code_execution.sandbox import SandboxCondition, SandboxStateKind
from pyrit.models.code_execution import _ProgramInvocation

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit._code_execution.sandbox import Sandbox
    from pyrit.models.code_execution import _ProgramExecutionEvent


class SandboxContractError(AssertionError):
    """An adapter broke a rule of the ``Sandbox`` contract."""


@dataclass(frozen=True)
class SandboxProbes:
    """
    Programs, in one of a sandbox's runtimes, that the contract checks run.

    Each builder returns program text. ``print_text`` writes its text to standard output as UTF-8;
    ``print_repeated`` writes one ASCII character many times; ``fail_with`` writes ASCII text to standard
    error and exits with a code; ``echo_arguments`` prints each argument on its own line; ``echo_stdin``
    copies standard input to standard output; ``write_marker`` writes text to a file, relative to the
    working directory, and ``read_marker`` prints that file, or nothing if it is missing; ``sleep_then_write``
    sleeps, then writes a marker, which shows whether a run past its timeout was really stopped.
    ``comment_prefix`` starts a comment, which pads a program to a size.
    """

    runtime_profile_name: str
    print_text: Callable[[str], str]
    print_repeated: Callable[[str, int], str]
    fail_with: Callable[[str, int], str]
    echo_arguments: str
    echo_stdin: str
    write_marker: Callable[[str, str], str]
    read_marker: Callable[[str], str]
    sleep_then_write: Callable[[float, str, str], str]
    comment_prefix: str = "#"

    @classmethod
    def python(cls, *, runtime_profile_name: str = "python") -> SandboxProbes:
        """
        Return probes for a Python 3 runtime.

        Args:
            runtime_profile_name (str): The sandbox's Python runtime. Defaults to ``"python"``.

        Returns:
            SandboxProbes: The probes.
        """
        return cls(
            runtime_profile_name=runtime_profile_name,
            print_text=lambda text: f"import sys\nsys.stdout.buffer.write({text!r}.encode('utf-8'))\n",
            print_repeated=lambda character, count: f"import sys\nsys.stdout.write({character!r} * {count})\n",
            fail_with=lambda text, code: f"import sys\nsys.stderr.write({text!r})\nsys.exit({code})\n",
            echo_arguments="import sys\nsys.stdout.write('\\n'.join(sys.argv[1:]))\n",
            echo_stdin="import sys\nsys.stdout.write(sys.stdin.read())\n",
            write_marker=lambda name, text: (
                f"from pathlib import Path\nPath({name!r}).write_text({text!r}, encoding='utf-8')\n"
            ),
            read_marker=lambda name: (
                "import sys\nfrom pathlib import Path\n"
                f"path = Path({name!r})\n"
                "sys.stdout.write(path.read_text(encoding='utf-8') if path.exists() else '')\n"
            ),
            sleep_then_write=lambda seconds, name, text: (
                f"import time\nfrom pathlib import Path\ntime.sleep({seconds!r})\n"
                f"Path({name!r}).write_text({text!r}, encoding='utf-8')\n"
            ),
        )


async def check_sandbox_contract_async(
    *, sandbox: Sandbox, probes: SandboxProbes, timeout_probe_seconds: float = 2.0
) -> None:
    """
    Check that an adapter honors the ``Sandbox`` contract by running real programs in it.

    Use a sandbox you can reset or discard: the checks write files, and reset the sandbox when it supports
    reset. They take a few seconds, plus a run that times out and, for an ``environment`` sandbox, a wait
    to show that run was stopped. The session is released at the end, pass or fail.
    Custom probe programs must fit ``max_input_bytes``, including any arguments and standard input.

    Args:
        sandbox (Sandbox): The adapter to check, connected to a live sandbox.
        probes (SandboxProbes): Programs in one of the sandbox's runtimes.
        timeout_probe_seconds (float): The timeout of the run that must time out. Defaults to 2 seconds.

    Raises:
        SandboxContractError: Naming the first rule the adapter breaks.
    """
    _require(
        probes.runtime_profile_name in sandbox.get_runtime_profile_names(),
        f"The probes use runtime {probes.runtime_profile_name!r}, which the sandbox does not offer.",
    )
    try:
        await _check_all_async(sandbox=sandbox, probes=probes, timeout_probe_seconds=timeout_probe_seconds)
    finally:
        with contextlib.suppress(SandboxBusyError):
            await sandbox.release_async()


async def _check_all_async(*, sandbox: Sandbox, probes: SandboxProbes, timeout_probe_seconds: float) -> None:
    capabilities = sandbox.get_capabilities()
    identity = sandbox._build_identifier().hash
    await _check_round_trip_async(sandbox=sandbox, probes=probes)
    _require(
        sandbox._build_identifier().hash == identity,
        "An identifier must hold static configuration only; it changed once the sandbox was used.",
    )
    await _check_arguments_and_input_async(sandbox=sandbox, probes=probes)
    await _check_status_async(sandbox=sandbox)
    if capabilities.state_kind is SandboxStateKind.ENVIRONMENT:
        await _check_state_persists_async(sandbox=sandbox, probes=probes)
        await _check_session_hooks_async(sandbox=sandbox, probes=probes)
    else:
        await _check_nothing_persists_async(sandbox=sandbox, probes=probes)
    if capabilities.supports_reset:
        await _check_reset_async(sandbox=sandbox, probes=probes)
    await _check_limits_async(sandbox=sandbox, probes=probes)
    await _check_timeout_async(sandbox=sandbox, probes=probes, timeout_seconds=timeout_probe_seconds)


async def _check_round_trip_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    limit = sandbox.get_capabilities().max_output_bytes
    text = "contract check: h\u00e9llo \u2713"
    if len(text.encode("utf-8")) > limit:
        text = "\u00e9" if limit >= 2 else "x"
    event = await _run_async(sandbox=sandbox, probes=probes, program=probes.print_text(text), check="round trip")
    _require(event.exit_code == 0, f"A program that succeeds must exit 0, not {event.exit_code}: {event.stderr!r}")
    _require(_lines(event.stdout) == text, f"Standard output must come back exactly as UTF-8; got {event.stdout!r}.")
    failure = "contract failure"[:limit]
    event = await _run_async(sandbox=sandbox, probes=probes, program=probes.fail_with(failure, 3), check="exit code")
    _require(event.exit_code == 3, f"The program's own exit code must come back; expected 3, got {event.exit_code}.")
    _require(failure in event.stderr, f"Standard error must come back; got {event.stderr!r}.")
    _require(event.stdout == "", f"Standard output must not include standard error; got {event.stdout!r}.")


async def _check_arguments_and_input_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    capabilities = sandbox.get_capabilities()
    arguments = ("alpha", "two words", 'quote\'s "mark"')
    argument_limit = capabilities.max_input_bytes - len(probes.echo_arguments.encode("utf-8"))
    if capabilities.max_arguments_bytes is not None:
        argument_limit = min(argument_limit, capabilities.max_arguments_bytes)
    if (
        sum(len(argument.encode("utf-8")) for argument in arguments) > argument_limit
        or len("\n".join(arguments).encode("utf-8")) > capabilities.max_output_bytes
    ):
        arguments = ("x",)
    event = await _run_async(
        sandbox=sandbox, probes=probes, program=probes.echo_arguments, arguments=arguments, check="arguments"
    )
    _require(_lines(event.stdout) == "\n".join(arguments), f"Arguments must reach the program; got {event.stdout!r}.")
    input_limit = min(
        capabilities.max_output_bytes,
        capabilities.max_input_bytes - len(probes.echo_stdin.encode("utf-8")),
    )
    _require(input_limit >= 1, "The stdin probe must fit max_input_bytes with at least one byte of standard input.")
    text = "line one\nline two\n"[:input_limit]
    event = await _run_async(sandbox=sandbox, probes=probes, program=probes.echo_stdin, stdin=text, check="stdin")
    _require(_lines(event.stdout) == text, f"Standard input must reach the program; got {event.stdout!r}.")


async def _check_status_async(*, sandbox: Sandbox) -> None:
    view = await sandbox.get_status_async()
    _require(view.status_is_current, "The status must be readable when nothing holds the sandbox.")
    _require(
        view.condition is SandboxCondition.READY,
        f"A sandbox under test must report ready; it reports {view.condition.value}: {view.status.reason}",
    )


async def _check_state_persists_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    name, token = await _write_marker_async(sandbox=sandbox, probes=probes)
    _require(
        await _read_marker_async(sandbox=sandbox, probes=probes, name=name) == token,
        "An environment sandbox must keep files between calls.",
    )
    await sandbox.release_async()
    _require(
        await _read_marker_async(sandbox=sandbox, probes=probes, name=name) == token,
        "An environment sandbox's files must survive closing its session.",
    )


async def _check_nothing_persists_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    name, _ = await _write_marker_async(sandbox=sandbox, probes=probes)
    _require(
        await _read_marker_async(sandbox=sandbox, probes=probes, name=name) == "",
        "A call sandbox must not keep files between calls.",
    )


async def _check_session_hooks_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    await sandbox.release_async()
    await _run_async(sandbox=sandbox, probes=probes, program=probes.print_text(""), check="session open")
    session_id = sandbox._session_id
    if session_id is None:
        raise SandboxContractError("Opening an environment session must retain its identifier.")
    await sandbox.release_async()
    _require(
        (await sandbox.get_status_async()).condition is SandboxCondition.READY,
        "Closing a session must succeed; cleanup could not be confirmed.",
    )
    try:
        await sandbox._call_control_async(
            sandbox._close_session_async(session_id=session_id), action="close a closed session"
        )
    except Exception as error:
        raise SandboxContractError(f"Closing a closed session must succeed; it raised {error!r}.") from error
    try:
        await sandbox._call_control_async(
            sandbox._execute_async(
                session_id=session_id,
                runtime_profile_name=probes.runtime_profile_name,
                program=probes.print_text("must not run"),
                arguments=(),
                stdin=None,
                timeout_seconds=sandbox.get_capabilities().default_timeout_seconds,
            ),
            action="run in a closed session",
        )
    except SandboxSessionLostError:
        return
    except Exception as error:
        raise SandboxContractError(
            f"A run in a closed session must raise SandboxSessionLostError, so PyRIT knows nothing ran; "
            f"it raised {error!r}."
        ) from error
    raise SandboxContractError("A run in a closed session must raise SandboxSessionLostError; it ran.")


async def _check_reset_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    name, token = await _write_marker_async(sandbox=sandbox, probes=probes)
    revision = (await sandbox.get_status_async()).status.revision
    _require(revision is not None, "A sandbox that resets must report its revision, so a reset can pin it.")
    try:
        await sandbox.reset_async(expected_revision=f"pyrit-contract-unexpected-{uuid.uuid4().hex}")
    except SandboxRevisionChangedError:
        pass
    except SandboxExecutionError as error:
        raise SandboxContractError(
            f"A reset to another revision must raise SandboxRevisionChangedError; got {error!r}."
        ) from error
    else:
        raise SandboxContractError("A reset to another revision must be refused with SandboxRevisionChangedError.")
    _require(
        await _read_marker_async(sandbox=sandbox, probes=probes, name=name) == token,
        "A refused reset must leave the sandbox unchanged.",
    )
    await sandbox.reset_async(expected_revision=revision)
    _require(
        await _read_marker_async(sandbox=sandbox, probes=probes, name=name) == "",
        "A reset must remove files written since the baseline.",
    )


async def _check_limits_async(*, sandbox: Sandbox, probes: SandboxProbes) -> None:
    capabilities = sandbox.get_capabilities()
    text = "padded"[: capabilities.max_output_bytes]
    program = probes.print_text(text)
    padding = (
        capabilities.max_input_bytes - len(program.encode("utf-8")) - len(probes.comment_prefix.encode("utf-8")) - 1
    )
    _require(padding >= 0, "max_input_bytes must fit the probe program.")
    program += probes.comment_prefix + "x" * padding + "\n"
    event = await _run_async(sandbox=sandbox, probes=probes, program=program, check="largest program")
    _require(event.stdout == text, f"A program of max_input_bytes ({capabilities.max_input_bytes}) must run.")
    limit = capabilities.max_output_bytes
    event = await _run_async(sandbox=sandbox, probes=probes, program=probes.print_repeated("x", limit), check="output")
    _require(
        event.stdout == "x" * limit and not event.stdout_truncated,
        f"Output of max_output_bytes ({limit}) must come back whole; got {len(event.stdout)} characters.",
    )
    program = probes.print_repeated("x", limit + 1024)
    event = await _run_async(sandbox=sandbox, probes=probes, program=program, check="long output")
    _require(event.stdout_truncated, "Output longer than max_output_bytes must be marked truncated.")
    _require(len(event.stdout) <= limit, f"No more than max_output_bytes ({limit}) of output may come back.")


async def _check_timeout_async(*, sandbox: Sandbox, probes: SandboxProbes, timeout_seconds: float) -> None:
    capabilities = sandbox.get_capabilities()
    timeout = min(timeout_seconds, capabilities.max_timeout_seconds, capabilities.default_timeout_seconds)
    name, _ = _marker()
    program = probes.sleep_then_write(timeout + 1, name, "late")
    started = time.monotonic()
    event = await _run_async(sandbox=sandbox, probes=probes, program=program, timeout_seconds=timeout, check="timeout")
    elapsed = time.monotonic() - started
    _require(event.timed_out and event.exit_code is None, "A run past its timeout must be marked timed out.")
    _require(elapsed < timeout + 20, f"A run with a {timeout:g} s timeout took {elapsed:.0f} s to come back.")
    if sandbox.get_capabilities().state_kind is SandboxStateKind.ENVIRONMENT:
        # The probe writes its marker one second after its timeout; give a stray run one more second.
        await asyncio.sleep(max(timeout + 2 - elapsed, 0.5))
        _require(
            await _read_marker_async(sandbox=sandbox, probes=probes, name=name) == "",
            "A run past its timeout must be stopped, not left running.",
        )


async def _write_marker_async(*, sandbox: Sandbox, probes: SandboxProbes) -> tuple[str, str]:
    name, token = _marker()
    token = token[: sandbox.get_capabilities().max_output_bytes]
    event = await _run_async(sandbox=sandbox, probes=probes, program=probes.write_marker(name, token), check="write")
    _require(event.exit_code == 0, f"Writing a file in the working directory must succeed: {event.stderr!r}")
    return name, token


async def _read_marker_async(*, sandbox: Sandbox, probes: SandboxProbes, name: str) -> str:
    event = await _run_async(sandbox=sandbox, probes=probes, program=probes.read_marker(name), check="read")
    _require(event.exit_code == 0, f"Reading a file in the working directory must succeed: {event.stderr!r}")
    return event.stdout


async def _run_async(
    *,
    sandbox: Sandbox,
    probes: SandboxProbes,
    program: str,
    check: str,
    arguments: tuple[str, ...] = (),
    stdin: str | None = None,
    timeout_seconds: float | None = None,
) -> _ProgramExecutionEvent:
    try:
        run = await sandbox.execute_async(
            program=program,
            runtime_profile_name=probes.runtime_profile_name,
            invocations=(_ProgramInvocation(arguments=arguments, stdin=stdin),),
            timeout_seconds=timeout_seconds,
        )
    except SandboxInputError as error:
        raise SandboxContractError(
            f"The {check} probe does not fit the sandbox's declared limits; supply smaller probe programs or inputs: "
            f"{error}"
        ) from error
    except SandboxExecutionError as error:
        raise SandboxContractError(f"The {check} check could not run: {error}") from error
    _require(len(run.events) == 1, f"The {check} check must produce one result; got {len(run.events)}.")
    return run.events[0]


def _marker() -> tuple[str, str]:
    token = uuid.uuid4().hex
    return f"pyrit_contract_{token}.txt", token


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SandboxContractError(message)
