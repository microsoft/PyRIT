# Sandboxed Tool Calls

To test a model that acts by running code, its code has to run somewhere isolated that can keep its work
across a conversation, and something other than the model has to say what that code did. PyRIT has three
pieces for this:

- a **`Sandbox`**, which runs programs on some infrastructure, such as a container, a VM or a hosted service;
- **`SandboxToolProvider`**, which turns a sandbox into tools a model can call, such as `run_python`;
- **`SandboxCheckScorer`**, which runs a fixed check program in the same sandbox after a turn and scores the
  response by what the check finds.

`pyrit._code_execution` is experimental, and so are the two classes built on it: their API may change
without a deprecation cycle.

## Give a model tools that run in a sandbox

Register each sandbox once, under a name, so that targets, scorers and operators share one `Sandbox`
object, and with it one session. An initializer is a good place to do it. Then give a tool-calling target the
sandbox's tools:

```python
from pyrit._code_execution import SandboxRegistry
from pyrit.prompt_target import OpenAIResponseTarget, SandboxToolProvider

SandboxRegistry.get_registry_singleton().register(MySandbox(...), name="windows-1")

target = OpenAIResponseTarget(
    tool_providers=[SandboxToolProvider(sandbox="windows-1")],
    max_tool_rounds=20,
)
```

The model sees ordinary tools in its own API:

| Tool | What it does |
|------|--------------|
| `run_<runtime>` | Runs a program once, such as `run_python` or `run_powershell`, with optional arguments, standard input and timeout |
| `write_file`, `read_file`, `list_dir` | Work with files in an `environment` or `session` sandbox whose `file_runtime_profile_name` names a Python 3 runtime |

Results are JSON. `run_<runtime>` returns the exit code, the output of each stream (cut at
`max_output_chars`, and marked when cut), whether the program timed out, and how long it took. A program
that fails is an ordinary result the model can read and fix.

Arguments are checked against the sandbox's limits before anything runs, so a call rejected there goes back to
the model as an error, with the reason, and is never recorded as invoked. If an adapter rejects a validated
request before execution, the tool raises `ToolInputError`; the target returns its reason with `invoked=False`
and lets the model correct the call. Executed programs that report an error still count as invoked.
When the sandbox itself fails, the tool raises `ToolInfrastructureError`, and `OpenAIResponseTarget`
ends the turn with an error response
instead of asking the model to fix a problem that isn't its own. Every call the model made still gets a
result, so the conversation's history stays valid; the model is told only that the tool's infrastructure
failed, while the error response, which is never replayed to the model, keeps the details. Its `ToolLoopStop`
metadata says why the turn ended. `max_tool_rounds` bounds how many responses with tool calls one send runs;
it defaults to 10.

When a script is done with its sandboxes, close them so no session is left open:

```python
await SandboxRegistry.get_registry_singleton().close_all_async()
```

`close_async` is final. To end a session and keep using the sandbox, use `release_async`, which can also
retry a close that couldn't be confirmed, even after `close_async`. It does not lift quarantine from
uncertain work, reset, or session-state loss; that needs a confirmed reset or `recover_async` after an
operator has verified backend recovery. Ending a `session` sandbox's session discards its state.

## Check what the code did

A model's claim that it succeeded is not evidence. A check program, run in the same sandbox after the turn,
verifies the effect itself:

```python
from pyrit.models import Equals, ProgramBehaves, ProgramCase, ProgramResult
from pyrit.score import SandboxCheckScorer

check = SandboxCheckScorer(
    sandbox="windows-1",
    check_program="import os\nprint('present' if os.path.exists('C:/task.flag') else 'absent')\n",
    condition=ProgramBehaves(
        runtime_profile_name="windows-python",
        cases=(ProgramCase(expected=ProgramResult(stdout=Equals(value="present"))),),
    ),
)
```

Use it as an attack's objective scorer. It scores the response being judged true when every case of the
check produces its expected result, and false when one doesn't. When the sandbox fails, or the check times
out, the score is undetermined, so an infrastructure problem is never counted as the model failing. The
response's own text never runs. The check's program and output are stored as an observation linked to the
score.

The check reads the sandbox as it is when it runs. Stored verdicts are never reused, including through
inverter, composite or fallback scorers. Isolation is the caller's job, since a later attempt could score
true on an earlier attempt's effect: run one attempt at a time per sandbox (`max_concurrency=1`), turn off automatic
retries of failed attempts (`max_attempts_on_failure=0`), and reset the sandbox between attempts. It needs a
sandbox whose state persists.

Checks use separate stateful acquisition, not the fresh-only `ProgramExecutionSource`. They share the
tools' exact registered `session` or `environment` sandbox and never release, reset or recover it.
Session-owned effects last only until that session ends; state loss or uncertain execution leaves the
score undetermined and preserves quarantine until the caller explicitly handles recovery.

The check runs as the same account as the model's code, so it must not trust anything the model could have
written. For example, run Python checks in isolated mode, so a module the model saved in the working
directory can't stand in for a standard one.

## The Docker example

`DockerSandboxClient` runs each program in a disposable, network-less container, so nothing persists
between calls, and it offers `run_<profile>` tools only. It needs a Docker daemon and pre-pulled, pinned
images; [Program execution and scoring](../scoring/7_program_execution_scorer.ipynb) shows how to configure
it. PyRIT includes it as an example of an adapter, not as a hardened sandbox.

```python
from pyrit._code_execution import DockerRuntimeProfile, DockerSandboxClient
from pyrit.prompt_target import OpenAIResponseTarget, SandboxToolProvider

docker = DockerSandboxClient(
    profiles={
        "python": DockerRuntimeProfile(
            image="python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1",
            command=("python3", "-I"),
        )
    }
)
target = OpenAIResponseTarget(tool_providers=[SandboxToolProvider(sandbox=docker)])
```

## Bring your own sandbox

An adapter is a small `Sandbox` subclass that translates a few calls into an infrastructure's own API.
PyRIT does the rest, so every sandbox behaves the same:

```python
from pyrit._code_execution import ProcessResult, Sandbox, SandboxCapabilities, SandboxStatus


class MySandbox(Sandbox):
    def _build_identifier(self): ...  # static configuration only, never session ids or secrets
    def _get_capabilities(self) -> SandboxCapabilities: ...  # runtimes, state kind, limits

    async def _execute_async(
        self, *, session_id, runtime_profile_name, program, arguments, stdin, timeout_seconds
    ) -> ProcessResult: ...

    async def _open_session_async(self, *, owner) -> str: ...  # not needed when nothing persists
    async def _close_session_async(self, *, session_id) -> None: ...  # closing twice must be safe
    async def _get_status_async(self) -> SandboxStatus: ...  # optional
    async def _reset_async(self, *, expected_revision) -> None: ...  # optional; returns when done
```

The capabilities name the runtimes (optionally with a `tool_name`, such as `powershell` for a runtime
called `windows-powershell`), the limits, and the **state kind**, which tells PyRIT whether work persists,
and so how to share the sandbox:

| State kind | Meaning | What PyRIT does |
|------------|---------|-----------------|
| `environment` | State lives in the machine and survives closing a session | One shared session, which callers take turns using |
| `session` | State ends with the session | One shared session; no automatic idle eviction, renewal or replacement |
| `call` | Nothing persists between calls | No sessions; calls run concurrently; no file tools |

Adapters raise these errors, so PyRIT knows what is safe to retry:

| Error | Meaning | What PyRIT does |
|-------|---------|-----------------|
| `SandboxInputError` | The request was invalid, and nothing ran | Reports it to the caller, or to the model |
| `SandboxBusyError` | The sandbox can't start the work right now, and nothing ran | Retries opening a session until the deadline; reports a busy run |
| `SandboxSessionLostError` | The session is gone, and nothing ran | Reopens and retries once for an `environment` sandbox |
| `SandboxStateLostError` | Session-owned state was lost | Reports the loss and requires explicit recovery or reset, without replacement |
| `SandboxRevisionChangedError` | A reset was refused, because the revision changed | Reports it to the caller |
| anything else | The outcome is unknown | Closes the session and never retries |

For every adapter, PyRIT validates the runtime, the size of the input and the timeout before anything
runs, and reports status, with its own session state, through `get_status_async`. For an `environment`
sandbox, it also:

- opens the session on first use, closes it after ten idle minutes, and renews it before a session time
  limit could cut a call short;
- lets one call at a time use the sandbox, first come first served, and waits up to five minutes for a turn;
- when a caller gives up, lets its running program finish, holding the sandbox so nothing runs beside it,
  and starts nothing more for it, not even a run that was waiting out a busy sandbox;
- quarantines the sandbox when it can't confirm that a session closed, that a run's processes stopped, or
  that a reset finished; `release_async` retries closes, while uncertain work or reset requires a confirmed
  reset or operator `recover_async`;
- resets only after the running call finishes, closing the session first, and records the outcome.

A `session` sandbox uses the same serialization and cancellation rules, but does not idle-close or renew
its session. A reset is attempted before closing, so a refused reset preserves its state.
The provider only shares the sandbox; it does not release, reset or recover it between model calls.

A `call` sandbox's calls run in their callers' tasks, so the adapter cleans up when a caller gives up.

`FakeSandbox` is an in-memory sandbox that never runs code. It stands in for a real one when you test code
that uses a sandbox. To check an adapter itself, run the contract-test kit against a live sandbox you can
discard or reset: it writes files, resets the sandbox when it can, and runs real programs to check the
declared limits, error mapping, state, status, reset, and closing twice.

```python
from pyrit._code_execution import SandboxProbes, check_sandbox_contract_async

await check_sandbox_contract_async(sandbox=MySandbox(...), probes=SandboxProbes.python())
```
