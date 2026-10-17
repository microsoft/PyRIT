# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
# ---

# %% [markdown]
# # Program-execution scoring
#
# `ProgramExecutionScorer` answers "Does this program behave as intended for these inputs?" It runs
# the program in an isolated sandbox and judges what the program did, not what a response claims
# it would do.
#
# The `ProgramBehaves` condition lists cases. Each `ProgramCase` pairs a `ProgramInvocation`
# (arguments and standard input) with a `ProgramResult` (exit code, and optional `stdout` and
# `stderr` matchers). The scorer splits them before anything runs: the runtime profile and the
# invocations go to the source, and the expected results stay with the scorer. The code that runs
# the program never sees which output passes.
#
# A `ProgramExecutionSource` asks an injected `SandboxClient` to run the program.
# `DockerSandboxClient` runs each invocation in a fresh container with no network, a read-only
# file system, no capabilities, an unprivileged user, and memory, CPU and process limits. A runtime
# profile is a trusted name for a digest-pinned image and a command; the program cannot pick either.
# Images are never pulled while scoring, so pull them first.
#
# A program that fails to compile, exits with the wrong code, prints the wrong output, times out, or
# exceeds a resource limit is a `False` verdict: the sandbox observed it. A sandbox that cannot run
# the program, or stops before every case ran, leaves the verdict undetermined, so broken
# infrastructure never looks like a failing program.
#
# This walkthrough needs a Docker daemon and the pinned image below
# (`docker pull python:3.12-slim`), and PyRIT's in-memory storage. It needs no model or credentials.

# %%
import httpx

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    ContentScorable,
    Equals,
    ProgramBehaves,
    ProgramCase,
    ProgramInvocation,
    ProgramResult,
    ScoringExpectation,
)
from pyrit.prompt_target import HTTPTarget
from pyrit.score import DockerRuntimeProfile, DockerSandboxClient, ProgramExecutionScorer, ProgramExecutionSource
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    load_defaults=False,
    env_files=[],
    silent=True,
)
memory = CentralMemory.get_memory_instance()

python_312 = DockerRuntimeProfile(
    image="python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1",
    command=("python3", "-I"),
)
sandbox = DockerSandboxClient(profiles={"python-3.12": python_312}, timeout_seconds=5)
scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=sandbox))

squares = ScoringExpectation(
    conditions=(
        ProgramBehaves(
            runtime_profile="python-3.12",
            cases=(
                ProgramCase(
                    invocation=ProgramInvocation(arguments=("3",)), expected=ProgramResult(stdout=Equals(value="9"))
                ),
                ProgramCase(
                    invocation=ProgramInvocation(stdin="12\n"), expected=ProgramResult(stdout=Equals(value="144"))
                ),
            ),
        ),
    )
)

# %% [markdown]
# ## Score a program directly
#
# A `ContentScorable` holds the program text. The program is run verbatim, once per case.

# %%
square = "import sys\nn = int(sys.argv[1]) if len(sys.argv) > 1 else int(sys.stdin.read())\nprint(n * n)\n"
doubles = "import sys\nn = int(sys.argv[1]) if len(sys.argv) > 1 else int(sys.stdin.read())\nprint(n * 2)\n"
loops = "while True:\n    pass\n"

for name, program in (("square", square), ("doubles", doubles), ("loops", loops)):
    score = (await scorer.score_async(scorable=ContentScorable(value=program), expectation=squares))[0]  # type: ignore
    print(f"{name}: {score.get_value()} - {score.score_rationale}")

# %% [markdown]
# ## A sandbox failure is not a program failure
#
# Here the profile names an image that is not on the machine, so no container starts. The
# observation is an acquisition error and the score is undetermined, not `False`.

# %%
missing_image = DockerSandboxClient(
    profiles={"python-3.12": DockerRuntimeProfile(image="python@sha256:" + "0" * 64, command=("python3", "-I"))}
)
unavailable = (
    await ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=missing_image)).score_async(  # type: ignore
        scorable=ContentScorable(value=square), expectation=squares
    )
)[0]
print(f"{unavailable.status.value}: {unavailable.score_rationale}")

# %% [markdown]
# ## Re-judge saved evidence
#
# The observation keeps each invocation's exit state, a bounded copy of both streams, the resolved
# command, and the image digest. Replay judges that evidence against new expected results without
# running the program again. The invocations must match the ones that ran.

# %%
passing = (await scorer.score_async(scorable=ContentScorable(value=square), expectation=squares))[0]  # type: ignore
saved = (await memory.get_observations_async(observation_ids=passing.observation_ids))[0]
for event in saved.payload.events:
    print(f"case {event.case_index}: {event.resolved_command} exit {event.exit_code}, stdout {event.stdout!r}")

stricter = ScoringExpectation(
    conditions=(
        ProgramBehaves(
            runtime_profile="python-3.12",
            cases=(
                ProgramCase(
                    invocation=ProgramInvocation(arguments=("3",)), expected=ProgramResult(stdout=Equals(value="9"))
                ),
                ProgramCase(
                    invocation=ProgramInvocation(stdin="12\n"), expected=ProgramResult(stdout=Equals(value="145"))
                ),
            ),
        ),
    )
)
replayed = (await scorer.score_observation_async(observation=saved, expectation=stricter))[0]  # type: ignore
print(f"Replayed against a stricter expectation: {replayed.get_value()} - {replayed.score_rationale}")

# %% [markdown]
# ## Score a generated program through an attack
#
# The attack scores its response as a program. This local target returns code as its whole response.
# A response that wraps code in explanation is run as it is and fails its cases; the scorer does not
# guess where the code starts.


# %%
def coder(*, program: str) -> HTTPTarget:
    """Return a local target that answers every prompt with the same program."""
    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: coder.test\n\n{PROMPT}",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=program)),
    )


for name, program in (("square", square), ("doubles", doubles)):
    attack = PromptSendingAttack(
        objective_target=coder(program=program),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        max_attempts_on_failure=0,
    )
    result = await attack.execute_async(objective="write a program that squares its input", expectation=squares)  # type: ignore
    print(f"Target writes {name} -> outcome {result.outcome.value}")
