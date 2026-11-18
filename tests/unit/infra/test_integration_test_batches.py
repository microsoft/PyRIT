# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import yaml


def test_integration_batches_cover_test_directories() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    integration_root = repo_root / "tests" / "integration"
    pipeline = yaml.safe_load((repo_root / ".azuredevops" / "integration-tests.yml").read_text(encoding="utf-8"))
    job = next(job for job in pipeline["jobs"] if job.get("template") == "test-job-template.yml")
    batches = job["parameters"]["testBatches"]

    test_files = set(integration_root.rglob("test_*.py")) | set(integration_root.rglob("*_test.py"))
    assert test_files
    assert all(path.parent != integration_root for path in test_files), "Root-level integration tests need a batch"
    expected = {f"tests/integration/{path.relative_to(integration_root).parts[0]}" for path in test_files}
    assert len(batches) == len(set(batches)), "Integration batches must not overlap by duplication"
    assert set(batches) == expected
