# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE_PATH = REPO_ROOT / ".azuredevops" / "adversarial-benchmark.yml"


def _load_pipeline() -> dict:
    return yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))


def test_benchmark_defaults_to_quick_profile_with_full_profile_available() -> None:
    pipeline = _load_pipeline()
    parameters = {parameter["name"]: parameter for parameter in pipeline["parameters"]}
    steps = pipeline["jobs"][0]["steps"]
    resolve_profile = next(step for step in steps if step.get("displayName") == "Resolve benchmark profile")
    script = resolve_profile["bash"]

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
        assert parameters[override]["default"] == 0
    quick_profile = script[script.index("quick)") : script.index(";;", script.index("quick)"))]
    full_profile = script[script.index("full)") : script.index(";;", script.index("full)"))]
    assert "profile_max_dataset_size=24" in quick_profile
    assert "profile_tap_tree_width=2" in quick_profile
    assert "profile_tap_tree_depth=3" in quick_profile
    assert "profile_tap_branching_factor=2" in quick_profile
    assert "profile_tap_batch_size=2" in quick_profile
    assert "profile_max_dataset_size=120" in full_profile
    assert "profile_tap_tree_width=3" in full_profile
    assert "profile_tap_tree_depth=5" in full_profile
    assert "profile_tap_branching_factor=2" in full_profile
    assert "profile_tap_batch_size=10" in full_profile


def test_benchmark_cache_is_enabled_and_passed_to_scenario() -> None:
    pipeline = _load_pipeline()
    parameters = {parameter["name"]: parameter for parameter in pipeline["parameters"]}
    run_step = next(
        step
        for step in pipeline["jobs"][0]["steps"]
        if step.get("displayName") == "Run benchmark and capture result snapshot"
    )

    assert parameters["useCached"]["default"] is True
    assert '--use-cached "$USE_CACHED_INPUT"' in run_step["inputs"]["inlineScript"]
    assert run_step["env"]["USE_CACHED_INPUT"] == "${{ parameters.useCached }}"


def test_benchmark_passes_profile_tap_settings_to_scenario() -> None:
    pipeline = _load_pipeline()
    run_step = next(
        step
        for step in pipeline["jobs"][0]["steps"]
        if step.get("displayName") == "Run benchmark and capture result snapshot"
    )
    script = run_step["inputs"]["inlineScript"]

    assert '--tap-tree-width "$TAP_TREE_WIDTH_INPUT"' in script
    assert '--tap-tree-depth "$TAP_TREE_DEPTH_INPUT"' in script
    assert '--tap-branching-factor "$TAP_BRANCHING_FACTOR_INPUT"' in script
    assert '--tap-batch-size "$TAP_BATCH_SIZE_INPUT"' in script
    assert run_step["env"]["MAX_DATASET_SIZE_INPUT"] == "$(benchmarkMaxDatasetSize)"
    assert run_step["env"]["TAP_TREE_WIDTH_INPUT"] == "$(benchmarkTapTreeWidth)"
    assert run_step["env"]["TAP_TREE_DEPTH_INPUT"] == "$(benchmarkTapTreeDepth)"
    assert run_step["env"]["TAP_BRANCHING_FACTOR_INPUT"] == "$(benchmarkTapBranchingFactor)"
    assert run_step["env"]["TAP_BATCH_SIZE_INPUT"] == "$(benchmarkTapBatchSize)"


def test_benchmark_manifest_records_profile_and_effective_tap_settings() -> None:
    pipeline = _load_pipeline()
    diagnostics = next(
        step for step in pipeline["jobs"][0]["steps"] if step.get("displayName") == "Collect benchmark diagnostics"
    )
    script = diagnostics["bash"]

    assert '"benchmark_profile": os.environ["BENCHMARK_PROFILE_INPUT"]' in script
    assert '"tap_tree_width": int(os.environ["TAP_TREE_WIDTH_INPUT"])' in script
    assert '"tap_tree_depth": int(os.environ["TAP_TREE_DEPTH_INPUT"])' in script
    assert '"tap_branching_factor": int(os.environ["TAP_BRANCHING_FACTOR_INPUT"])' in script
    assert '"tap_batch_size": int(os.environ["TAP_BATCH_SIZE_INPUT"])' in script


def test_benchmark_cache_restores_same_branch_state_including_failed_runs() -> None:
    pipeline = _load_pipeline()
    steps = pipeline["jobs"][0]["steps"]
    conditional_steps = next(step for step in steps if "${{ if eq(parameters.useCached, true) }}" in step)
    restore = conditional_steps["${{ if eq(parameters.useCached, true) }}"][0]

    assert restore["task"] == "DownloadPipelineArtifact@2"
    assert restore["inputs"]["buildVersionToDownload"] == "latestFromBranch"
    assert restore["inputs"]["branchName"] == "$(Build.SourceBranch)"
    assert restore["inputs"]["allowPartiallySucceededBuilds"] is True
    assert restore["inputs"]["allowFailedBuilds"] is True
    assert restore["inputs"]["artifactName"] == "adversarial-benchmark-db"
    assert restore["inputs"]["targetPath"] == "$(Build.SourcesDirectory)/dbdata"


def test_benchmark_database_is_published_even_after_failure() -> None:
    pipeline = _load_pipeline()
    steps = pipeline["jobs"][0]["steps"]
    stage = next(step for step in steps if step.get("displayName") == "Stage reusable benchmark database")
    publish = next(step for step in steps if step.get("displayName") == "Publish reusable benchmark database")

    assert stage["condition"] == "always()"
    assert "##vso[task.setvariable variable=hasBenchmarkDatabase]true" in stage["bash"]
    assert "##vso[task.setvariable variable=hasBenchmarkDatabase]false" in stage["bash"]
    assert publish["task"] == "PublishPipelineArtifact@1"
    assert publish["condition"] == "and(always(), eq(variables['hasBenchmarkDatabase'], 'true'))"
    assert publish["inputs"]["artifactName"] == "adversarial-benchmark-db"
