# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def publication_patterns() -> list[str]:
    template = yaml.safe_load((REPO_ROOT / ".azuredevops" / "test-job-template.yml").read_text(encoding="utf-8"))
    publishers = [step for step in template["jobs"][0]["steps"] if step.get("task") == "PublishTestResults@2"]
    assert len(publishers) == 1
    publisher = publishers[0]
    assert publisher["condition"] == "always()"
    assert publisher["inputs"]["testResultsFormat"] == "JUnit"
    assert publisher["inputs"]["mergeTestResults"] is True
    patterns: object = publisher["inputs"]["testResultsFiles"]
    assert isinstance(patterns, str)
    return patterns.splitlines()


@pytest.mark.skipif(shutil.which("make") is None, reason="GNU Make is not installed")
@pytest.mark.parametrize(
    ("target", "default_xml"),
    [
        ("integration-test", "junit/test-results.xml"),
        ("end-to-end-test", "junit/test-results.xml"),
        ("partner-integration-test", "junit/test-results-partner.xml"),
    ],
)
@pytest.mark.parametrize("junit_xml", [None, "junit/test-results-1.xml", "junit/test-results-12.xml"])
def test_makefile_junit_defaults_and_overrides_are_published(
    *, target: str, default_xml: str, junit_xml: str | None, publication_patterns: list[str]
) -> None:
    command = ["make", "--dry-run", "--no-print-directory", target]
    if junit_xml is not None:
        command.append(f"JUNIT_XML={junit_xml}")
    result = subprocess.run(
        command, cwd=REPO_ROOT, check=True, capture_output=True, text=True, timeout=30, env=dict(os.environ)
    )
    output = junit_xml or default_xml
    assert re.findall(r"--junitxml=(\S+)", result.stdout) == [output]
    assert any(fnmatch.fnmatchcase(output, pattern) for pattern in publication_patterns)


@pytest.mark.parametrize(
    ("path", "published"),
    [
        ("junit/test-results.xml", True),
        ("junit/test-results-1.xml", True),
        ("junit/test-results-12.xml", True),
        ("junit/test-results-partner.xml", True),
        ("junit/other-results.xml", False),
        ("junit/partner-test-results.xml", False),
        ("junit/test-results-partner.txt", False),
        ("other/test-results-partner.xml", False),
    ],
)
def test_publication_preserves_existing_filename_scope(
    *, path: str, published: bool, publication_patterns: list[str]
) -> None:
    assert any(fnmatch.fnmatchcase(path, pattern) for pattern in publication_patterns) is published
