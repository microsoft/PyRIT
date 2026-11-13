# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import UUID

import pytest
from pydantic import ValidationError

from pyrit.models import DecompositionNode, DecompositionPath, Finding, FindingCreate, is_finding_path_complete
from pyrit.models.harm_category import HarmCategory

OPERATION_ID = UUID(int=1)


@pytest.mark.parametrize("severity", ["critical", "important", "moderate", "low", "informational"])
def test_finding_round_trips_named_severity(severity: str) -> None:
    finding = Finding(operation_id=OPERATION_ID, title="Assessment", severity=severity)
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
    assert finding.description == ""
    assert finding.created_at.utcoffset().total_seconds() == 0


def test_finding_round_trips_custom_classifications_without_normalizing_text() -> None:
    finding = Finding.model_validate(
        {
            "operation_id": OPERATION_ID,
            "title": "Assessment",
            "severity": "other",
            "severity_other": "  Team assessment  ",
            "harm_type": "Other",
            "harm_type_other": "  Team harm  ",
        }
    )
    assert finding.severity.value == "other"
    assert finding.severity_other == "  Team assessment  "
    assert finding.harm_type.value == "Other"
    assert finding.harm_type_other == "  Team harm  "
    assert Finding.model_validate_json(finding.model_dump_json()) == finding


def test_finding_harm_type_is_optional_and_accepts_every_canonical_category() -> None:
    assert FindingCreate(title="Optional", severity="low").harm_type is None
    for category in HarmCategory:
        finding = FindingCreate.model_validate(
            {
                "title": "Classified",
                "severity": "moderate",
                "harm_type": category.value,
                "harm_type_other": "Custom harm" if category == HarmCategory.OTHER else None,
            }
        )
        assert finding.harm_type == category


@pytest.mark.parametrize(
    "fields",
    [
        {"severity": "other"},
        {"severity": "other", "severity_other": " "},
        {"severity": "low", "severity_other": "Stray custom severity"},
        {"harm_type": "Other"},
        {"harm_type": "Other", "harm_type_other": " "},
        {"harm_type": "Malware", "harm_type_other": "Stray custom harm"},
        {"harm_type_other": "Custom without category"},
        {"harm_type": "Unknown category"},
    ],
)
def test_finding_rejects_inconsistent_classifications(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        FindingCreate.model_validate({"title": "Assessment", "severity": "low", **fields})


@pytest.mark.parametrize("title", ["", "  "])
def test_finding_rejects_blank_title(title: str) -> None:
    with pytest.raises(ValidationError):
        FindingCreate(title=title, severity="low")


def test_finding_rejects_unknown_input_and_requires_operation() -> None:
    with pytest.raises(ValidationError):
        FindingCreate(title="Assessment", severity="urgent")
    with pytest.raises(ValidationError):
        FindingCreate.model_validate({"title": "Assessment", "severity": "low", "operation": "Case"})
    with pytest.raises(ValidationError):
        Finding(title="Assessment", severity="low")


def test_finding_defaults_to_a_demonstrated_top_level_finding() -> None:
    finding = Finding(operation_id=OPERATION_ID, title="Assessment", severity="low")
    assert finding.parent_finding_id is None
    assert finding.demonstrated is True
    assert finding.paths == ()
    assert finding.decomposition_node == DecompositionNode(id=finding.id)


def test_finding_round_trips_its_decomposition() -> None:
    parent_id, first, second = UUID(int=2), UUID(int=3), UUID(int=4)
    finding = Finding(
        operation_id=OPERATION_ID,
        id=parent_id,
        title="Data exfiltration",
        severity="critical",
        parent_finding_id=UUID(int=5),
        demonstrated=False,
        paths=(DecompositionPath(name="Direct", steps=(first, second)), DecompositionPath(steps=(second,))),
    )
    restored = Finding.model_validate_json(finding.model_dump_json())
    assert restored == finding
    assert restored.decomposition_node == DecompositionNode(id=parent_id, parent_id=UUID(int=5), paths=finding.paths)


def test_finding_create_has_no_paths() -> None:
    with pytest.raises(ValidationError):
        FindingCreate.model_validate({"title": "Assessment", "severity": "low", "paths": []})


@pytest.mark.parametrize(
    ("parent", "paths", "message"),
    [
        (UUID(int=2), (), "its own parent"),
        (None, (DecompositionPath(steps=(UUID(int=2),)),), "step of its own path"),
        (
            None,
            (
                DecompositionPath(id=UUID(int=9), steps=(UUID(int=3),)),
                DecompositionPath(id=UUID(int=9), steps=(UUID(int=4),)),
            ),
            "distinct ID",
        ),
    ],
)
def test_finding_rejects_an_invalid_own_decomposition(
    parent: UUID | None, paths: tuple[DecompositionPath, ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Finding(
            operation_id=OPERATION_ID,
            id=UUID(int=2),
            title="Assessment",
            severity="low",
            parent_finding_id=parent,
            paths=paths,
        )


def test_path_is_complete_only_when_every_step_is_demonstrated() -> None:
    first, second = UUID(int=3), UUID(int=4)
    path = DecompositionPath(steps=(first, second))
    assert is_finding_path_complete(path=path, demonstrated={first: True, second: True})
    assert not is_finding_path_complete(path=path, demonstrated={first: True, second: False})
    assert not is_finding_path_complete(path=path, demonstrated={first: True})
