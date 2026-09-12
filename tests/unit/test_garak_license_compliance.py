# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_PATH = REPOSITORY_ROOT / "third_party" / "garak-provenance.json"
REQUIRED_NOTICE_TEXT = (
    "Apache-2.0",
    "modified by Microsoft Corporation",
    "THIRD_PARTY_NOTICES.txt",
)


def _load_provenance() -> dict[str, Any]:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def test_garak_provenance_uses_immutable_revision() -> None:
    provenance = _load_provenance()

    revision = provenance["revision"]
    assert len(revision) == 40
    assert all(character in "0123456789abcdef" for character in revision)


def test_garak_redistributed_files_have_attribution() -> None:
    provenance = _load_provenance()

    for entry in provenance["redistributed_files"]:
        file_path = REPOSITORY_ROOT / entry["path"]
        assert file_path.is_file(), f"Missing redistributed file: {entry['path']}"
        content = file_path.read_text(encoding="utf-8")
        for required_text in REQUIRED_NOTICE_TEXT:
            assert required_text in content, f"{entry['path']} is missing {required_text!r}"


def test_garak_local_datasets_do_not_use_moving_source_urls() -> None:
    provenance = _load_provenance()
    revision = provenance["revision"]
    local_dataset_dir = REPOSITORY_ROOT / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"
    manifest_paths = {entry["path"] for entry in provenance["redistributed_files"]}

    for file_path in local_dataset_dir.glob("*.prompt"):
        content = file_path.read_text(encoding="utf-8")
        assert "NVIDIA/garak/blob/main" not in content
        assert revision in content
        assert file_path.relative_to(REPOSITORY_ROOT).as_posix() in manifest_paths


def test_garak_license_files_are_configured_for_distribution() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (REPOSITORY_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "LICENSES/Apache-2.0.txt" in pyproject
    assert "THIRD_PARTY_NOTICES.txt" in pyproject
    assert "recursive-include LICENSES *.txt" in manifest
    assert "include THIRD_PARTY_NOTICES.txt" in manifest


def test_notice_task_includes_garak_notice() -> None:
    pipeline = (REPOSITORY_ROOT / ".azuredevops" / "component-governance.yml").read_text(encoding="utf-8")

    assert "additionaldata: $(System.DefaultWorkingDirectory)/THIRD_PARTY_NOTICES.txt" in pipeline
