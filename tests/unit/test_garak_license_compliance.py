# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GARAK_PROVENANCE_PATH = REPOSITORY_ROOT / "third_party" / "garak-provenance.json"
PROMPTINJECT_PROVENANCE_PATH = REPOSITORY_ROOT / "third_party" / "promptinject-provenance.json"
GARAK_REQUIRED_NOTICE_TEXT = (
    "Apache-2.0",
    "modified by Microsoft Corporation",
    "THIRD_PARTY_NOTICES.txt",
)
PROMPTINJECT_REQUIRED_NOTICE_TEXT = (
    "Copyright (c) 2020 Agency Enterprise, LLC",
    "licensed under MIT and modified by Microsoft Corporation",
    "THIRD_PARTY_NOTICES.txt",
)


def _load_provenance(*, path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_third_party_provenance_uses_immutable_revisions() -> None:
    for path in (GARAK_PROVENANCE_PATH, PROMPTINJECT_PROVENANCE_PATH):
        provenance = _load_provenance(path=path)

        revision = provenance["revision"]
        assert len(revision) == 40
        assert all(character in "0123456789abcdef" for character in revision)


def test_redistributed_files_have_license_specific_attribution() -> None:
    provenance_requirements = (
        (_load_provenance(path=GARAK_PROVENANCE_PATH), GARAK_REQUIRED_NOTICE_TEXT),
        (_load_provenance(path=PROMPTINJECT_PROVENANCE_PATH), PROMPTINJECT_REQUIRED_NOTICE_TEXT),
    )

    for provenance, required_notice_text in provenance_requirements:
        for entry in provenance["redistributed_files"]:
            file_path = REPOSITORY_ROOT / entry["path"]
            assert file_path.is_file(), f"Missing redistributed file: {entry['path']}"
            content = file_path.read_text(encoding="utf-8")
            for required_text in required_notice_text:
                assert required_text in content, f"{entry['path']} is missing {required_text!r}"


def test_garak_local_datasets_use_declared_immutable_source_urls() -> None:
    provenances = [
        _load_provenance(path=GARAK_PROVENANCE_PATH),
        _load_provenance(path=PROMPTINJECT_PROVENANCE_PATH),
    ]
    local_dataset_dir = REPOSITORY_ROOT / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"
    provenance_by_path = {
        entry["path"]: provenance for provenance in provenances for entry in provenance["redistributed_files"]
    }

    for file_path in local_dataset_dir.glob("*.prompt"):
        content = file_path.read_text(encoding="utf-8")
        relative_path = file_path.relative_to(REPOSITORY_ROOT).as_posix()
        assert relative_path in provenance_by_path
        provenance = provenance_by_path[relative_path]
        immutable_source_prefix = f"{provenance['repository']}/blob/{provenance['revision']}/"
        assert immutable_source_prefix in content


def test_third_party_license_files_are_configured_for_distribution() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (REPOSITORY_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "LICENSES/Apache-2.0.txt" in pyproject
    assert "LICENSES/PromptInject-MIT.txt" in pyproject
    assert "THIRD_PARTY_NOTICES.txt" in pyproject
    assert "third_party/garak-provenance.json" in pyproject
    assert "third_party/promptinject-provenance.json" in pyproject
    assert "recursive-include LICENSES *.txt" in manifest
    assert "include THIRD_PARTY_NOTICES.txt" in manifest
    assert "recursive-include third_party *.json" in manifest


def test_third_party_notice_is_complete_and_used_by_component_governance() -> None:
    notice = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    pipeline = (REPOSITORY_ROOT / ".azuredevops" / "component-governance.yml").read_text(encoding="utf-8")

    assert "garak source portions - Apache-2.0" in notice
    assert "PromptInject source portions - MIT" in notice
    assert "additionaldata: $(System.DefaultWorkingDirectory)/THIRD_PARTY_NOTICES.txt" in pipeline
