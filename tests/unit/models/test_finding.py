# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import UUID

import pytest
from pydantic import ValidationError

from pyrit.models import DecompositionNode, DecompositionPath, Finding, FindingCreate, is_finding_path_complete

OPERATION_ID = UUID(int=1)


def test_finding_round_trips_severity_and_field_values() -> None:
    finding = Finding(
        operation_id=OPERATION_ID, title="Assessment", severity="moderate", field_values={"harm_type": "Malware"}
    )
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
    assert FindingCreate(title="Assessment", severity="low").field_values == {}
    assert finding.created_at.utcoffset().total_seconds() == 0


def test_finding_strips_severity_drops_blank_field_values_and_keeps_other_text_exactly() -> None:
    finding = FindingCreate(
        title="  Leak  ", severity="  Sev 2.5  ", field_values={"steps": "", "owner": "   ", "notes": "  kept  "}
    )
    assert finding.title == "  Leak  "
    assert finding.severity == "Sev 2.5"
    assert finding.field_values == {"notes": "  kept  "}


@pytest.mark.parametrize(
    "data",
    [
        {"title": " ", "severity": "low"},
        {"title": "Leak", "severity": " "},
        {"title": "Leak", "severity": "low", "description": "x"},
        {"title": "Leak", "severity": "low", "field_values": {"steps": 3}},
    ],
)
def test_finding_rejects_invalid_input(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FindingCreate.model_validate(data)


def test_finding_requires_an_operation() -> None:
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
