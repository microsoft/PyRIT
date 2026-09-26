# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE_PATH = REPO_ROOT / ".azuredevops" / "adversarial-benchmark.yml"

_SET_VARIABLE_PATTERN = re.compile(r"task\.setvariable variable=(\w+)\]")
_VARIABLE_REFERENCE_PATTERN = re.compile(r"\$\((benchmark\w+)\)")


def _load_pipeline() -> dict:
    return yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))


def _steps() -> list:
    return _load_pipeline()["jobs"][0]["steps"]


def _step(display_name: str) -> dict:
    return next(step for step in _steps() if step.get("displayName") == display_name)


def _step_script(step: dict) -> str:
    return step.get("bash") or step["inputs"]["inlineScript"]


def _emitted_benchmark_variables() -> set[str]:
    return set(_SET_VARIABLE_PATTERN.findall(_step_script(_step("Resolve benchmark profile"))))


@pytest.mark.parametrize(
    "display_name",
    ["Run benchmark and capture result snapshot", "Collect benchmark diagnostics"],
)
def test_every_consumed_benchmark_variable_is_emitted_by_profile_resolution(display_name: str) -> None:
    """Any ``$(benchmark*)`` a step reads must be set by the resolve step, or it expands empty."""
    consumed = {
        variable
        for value in _step(display_name).get("env", {}).values()
        for variable in _VARIABLE_REFERENCE_PATTERN.findall(str(value))
    }

    assert consumed, f"{display_name} is expected to consume resolved benchmark variables"
    assert consumed <= _emitted_benchmark_variables()


def test_run_step_reads_every_environment_variable_it_declares() -> None:
    """A declared-but-unread env var is dead profile plumbing; an unset one expands empty."""
    run_step = _step("Run benchmark and capture result snapshot")
    script = _step_script(run_step)

    for name in run_step["env"]:
        assert f"${name}" in script, f"{name} is declared but never read by the run step"


def test_benchmark_defaults_to_quick_profile_with_full_profile_available() -> None:
    pipeline = _load_pipeline()
    parameters = {parameter["name"]: parameter for parameter in pipeline["parameters"]}
    resolve_profile = _step("Resolve benchmark profile")

    assert resolve_profile["condition"] == "always()"
    assert parameters["benchmarkProfile"]["default"] == "quick"
    assert parameters["benchmarkProfile"]["values"] == ["quick", "full"]
    for override in (
        "maxDatasetSize",
        "tapTreeWidth",
        "tapTreeDepth",
        "tapBranchingFactor",
        "tapBatchSize",
    ):
        assert parameters[override]["default"] == 0, "0 is the sentinel meaning 'use the profile value'"


def test_benchmark_cache_is_enabled_and_passed_to_scenario() -> None:
    pipeline = _load_pipeline()
    parameters = {parameter["name"]: parameter for parameter in pipeline["parameters"]}
    run_step = _step("Run benchmark and capture result snapshot")

    assert parameters["useCached"]["default"] is True
    assert '--use-cached "$USE_CACHED_INPUT"' in _step_script(run_step)
    assert run_step["env"]["USE_CACHED_INPUT"] == "${{ parameters.useCached }}"


def test_benchmark_passes_tap_tuning_through_the_generic_technique_args_flag() -> None:
    """TAP tuning is pipeline policy; the scenario only sees technique-agnostic overrides."""
    resolve_script = _step_script(_step("Resolve benchmark profile"))
    run_script = _step_script(_step("Run benchmark and capture result snapshot"))

    for argument in ("tree_width", "tree_depth", "branching_factor", "batch_size"):
        assert f'"tap.{argument}=' in resolve_script

    assert "--technique-args" in run_script
    assert "--tap-" not in run_script


def test_benchmark_cache_restores_same_branch_state_including_failed_runs() -> None:
    conditional_steps = next(step for step in _steps() if "${{ if eq(parameters.useCached, true) }}" in step)
    restore = conditional_steps["${{ if eq(parameters.useCached, true) }}"][0]

    assert restore["task"] == "DownloadPipelineArtifact@2"
    assert restore["inputs"]["buildVersionToDownload"] == "latestFromBranch"
    assert restore["inputs"]["branchName"] == "$(Build.SourceBranch)"
    assert restore["inputs"]["allowPartiallySucceededBuilds"] is True
    assert restore["inputs"]["allowFailedBuilds"] is True
    assert restore["inputs"]["artifactName"] == "adversarial-benchmark-db"
    assert restore["inputs"]["targetPath"] == "$(Build.SourcesDirectory)/dbdata"


def test_benchmark_database_is_published_even_after_failure() -> None:
    stage = _step("Stage reusable benchmark database")
    publish = _step("Publish reusable benchmark database")

    assert stage["condition"] == "always()"
    assert "##vso[task.setvariable variable=hasBenchmarkDatabase]true" in stage["bash"]
    assert "##vso[task.setvariable variable=hasBenchmarkDatabase]false" in stage["bash"]
    assert publish["task"] == "PublishPipelineArtifact@1"
    assert publish["condition"] == "and(always(), eq(variables['hasBenchmarkDatabase'], 'true'))"
    assert publish["inputs"]["artifactName"] == "adversarial-benchmark-db"
