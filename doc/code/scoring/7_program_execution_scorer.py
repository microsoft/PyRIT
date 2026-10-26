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
# # Program execution and scoring
#
# `pyrit._code_execution` is experimental; its API may change without a deprecation cycle.
# Its shared data types in `pyrit.models.code_execution` are also private and experimental.
# The caller provides a Docker daemon and this exact pre-pulled image:
# `docker pull python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
# PyRIT does not start Docker or pull images. This example needs no model credentials.
# Docker must support cgroup memory, swap, CPU, and process limits. Runtime images must not
# declare volumes. Unsupported backends and images are rejected before execution.
#
# ## Execute without scoring or memory
#
# A runtime profile name selects a trusted client configuration, not an arbitrary image.
# The client runs code verbatim and returns bounded output and exit information.
# Targets, converters, and generators can use this same interface.

# %%
from pyrit._code_execution import DockerRuntimeProfile, DockerSandboxClient
from pyrit.models.code_execution import _ProgramInvocation

client = DockerSandboxClient(
    profiles={
        "python": DockerRuntimeProfile(
            image="python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1",
            command=("python3", "-I"),
        )
    }
)
program = "import sys\nprint(int(sys.argv[1]) ** 2)\n"
invocation = _ProgramInvocation(arguments=("3",))
assert "python" in client.get_runtime_profile_names()
run = await client.execute_async(  # type: ignore
    program=program, runtime_profile_name="python", invocations=(invocation,)
)
assert run.events[0].exit_code == 0
assert run.events[0].stdout == "9\n"
print(f"Direct execution: {run.events[0].stdout.strip()}")

# %% [markdown]
# ## Score and save the evidence
#
# The source receives only code and invocation inputs; expected results stay with the scorer.
# Every `score_async` call starts a fresh execution, identified by its saved observation ID.
# Wrong output, unexpected exits, and enforced resource/time limits fail a case. Infrastructure
# failures or insufficient evidence leave the verdict undetermined.

# %%
from pyrit.memory import CentralMemory
from pyrit.models import ContentScorable, Equals, ProgramBehaves, ProgramCase, ProgramResult, ScoringExpectation
from pyrit.score import ProgramExecutionScorer, ProgramExecutionSource
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY, load_defaults=False, env_files=[], silent=True
)
case = ProgramCase(invocation=invocation, expected=ProgramResult(stdout=Equals(value="9")))
expectation = ScoringExpectation(conditions=(ProgramBehaves(runtime_profile_name="python", cases=(case,)),))
scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=client))
score = (await scorer.score_async(scorable=ContentScorable(value=program), expectation=expectation))[0]  # type: ignore
assert score.get_value() is True
print(f"Score: {score.get_value()} - {score.score_rationale}")

# %% [markdown]
# ## Replay without another execution
#
# Replay uses the saved outputs, not the Docker client. Expected results may change, but the
# runtime profile name and invocation inputs must still match the saved run.

# %%
memory = CentralMemory.get_memory_instance()
observation = (await memory.get_observations_async(observation_ids=score.observation_ids))[0]
different_case = ProgramCase(invocation=invocation, expected=ProgramResult(stdout=Equals(value="8")))
different_expectation = ScoringExpectation(
    conditions=(ProgramBehaves(runtime_profile_name="python", cases=(different_case,)),)
)
replayed = (
    await scorer.score_observation_async(observation=observation, expectation=different_expectation)  # type: ignore
)[0]
assert replayed.get_value() is False
assert replayed.observation_ids == score.observation_ids
print(f"Replay: {replayed.get_value()} - {replayed.score_rationale}")

# %% [markdown]
# Real execution and fault coverage is in `tests/integration/score/test_program_execution_scorer_integration.py`.
# Opt in with `RUN_ALL_TESTS=true`; the tests require the daemon and pinned image above.
