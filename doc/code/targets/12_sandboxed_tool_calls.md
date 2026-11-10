# Sandboxed Tool Calls

To test a model that acts by running code, its code has to run somewhere isolated. PyRIT has two pieces
for this:

- a **`SandboxClient`**, which runs programs on some infrastructure, such as a container, a VM or a hosted
  service, and declares its runtimes and limits;
- **`SandboxToolProvider`**, which turns a sandbox into tools a model can call, such as `run_python`.

`pyrit._code_execution` is experimental, and so is `SandboxToolProvider`, which is built on it: their API
may change without a deprecation cycle.

## Give a model tools that run in a sandbox

`DockerSandboxClient` runs each program in a disposable, network-less container, so nothing persists
between calls. It needs a Docker daemon and pre-pulled, pinned images;
[Program execution and scoring](../scoring/7_program_execution_scorer.ipynb) shows how to configure it.
PyRIT includes it as an example of a sandbox client, not as a hardened sandbox.

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
target = OpenAIResponseTarget(tool_providers=[SandboxToolProvider(sandbox=docker)], max_tool_rounds=20)
```

Each runtime the sandbox declares becomes a `run_<runtime>` tool, such as `run_python` or `run_powershell`.
A runtime can set `tool_name` to choose the name, such as `powershell` for a runtime called
`windows-powershell`. The tool takes the program, with optional arguments, standard input and timeout.

Results are JSON: the exit code, the output of each stream (cut at `max_output_chars`, and marked when cut),
whether the program timed out or exceeded a resource limit, and how long it took. A program that fails is
an ordinary result the model can read and fix.

Arguments are checked against the sandbox's declared limits before anything runs, so a call rejected there
goes back to the model as an error, with the reason, and is never recorded as invoked. If the sandbox
rejects a request itself (`SandboxInputError`), the tool raises `ToolInputError`; the target returns its
reason with `invoked=False` and lets the model correct the call. Executed programs that report an error
still count as invoked.

When the sandbox itself fails (`SandboxExecutionError`), the tool raises `ToolInfrastructureError`, and
`OpenAIResponseTarget` ends the turn with an error response instead of asking the model to fix a problem
that isn't its own. Every call the model made still gets a result, so the conversation's history stays
valid; the model is told only that the tool's infrastructure failed, while the error response, which is
never replayed to the model, keeps the details. Its `ToolLoopStop` metadata says why the turn ended.
`max_tool_rounds` bounds how many responses with tool calls one send runs; it defaults to 10.

## Bring your own sandbox

A sandbox client implements the `SandboxClient` protocol:

```python
from pyrit._code_execution import RuntimeProfile, SandboxCapabilities


class MySandbox:
    def get_identifier(self): ...  # static configuration only, never session ids or secrets
    def get_runtime_profile_names(self): ...
    def get_capabilities(self) -> SandboxCapabilities: ...  # runtimes and per-call limits

    async def execute_async(self, *, program, runtime_profile_name, invocations, timeout_seconds=None):
        timeout = self.get_capabilities().validate_request(
            program=program,
            runtime_profile_name=runtime_profile_name,
            invocations=invocations,
            timeout_seconds=timeout_seconds,
        )
        ...  # run each invocation in a fresh environment and return a _SandboxRun
```

`SandboxCapabilities` names the runtimes (optionally with a `tool_name` and a `description` for the model),
says where code runs, and declares the limits of each call: the size of the program, its input and its
arguments, the output kept per stream, and the default and maximum timeout. `validate_request` raises
`SandboxInputError` for a request that doesn't fit, so call it before running anything. Raise
`SandboxExecutionError` when the sandbox itself can't run the program; a program that fails, times out or
exceeds a limit is a result, not an error.

Each invocation runs in a fresh environment: a sandbox client keeps nothing between calls.
