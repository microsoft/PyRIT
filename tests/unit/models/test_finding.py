# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import re
from uuid import UUID

import pytest
from pydantic import ValidationError

from pyrit.models import (
    DecompositionNode,
    DecompositionPath,
    Finding,
    FindingCreate,
    is_finding_path_complete,
)
from pyrit.models.harm_category import HarmCategory

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
        {"title": "Leak", "severity": "low", "notes": "x"},
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


@pytest.mark.parametrize("module_name", ["pyrit.models", "pyrit.models.finding"])
def test_finding_severity_is_deprecated_and_keeps_its_values(module_name: str) -> None:
    module = importlib.import_module(module_name)
    with pytest.warns(DeprecationWarning, match=rf"{re.escape(module_name)}\.FindingSeverity .* 1\.5\.0"):
        severity = module.FindingSeverity
    assert [member.value for member in severity] == [
        "critical",
        "important",
        "moderate",
        "low",
        "informational",
        "other",
    ]
    assert FindingCreate(title="Leak", severity=severity.CRITICAL).severity == "critical"


@pytest.mark.parametrize(
    ("old_fields", "severity", "field_values"),
    [
        (
            {"severity": "other", "severity_other": "  Sev 2.5 ", "harm_type": "Malware", "description": "Notes"},
            "Sev 2.5",
            {"harm_type": "Malware", "description": "Notes"},
        ),
        (
            {"severity": "low", "harm_type": HarmCategory.OTHER, "harm_type_other": "Model theft", "description": " "},
            "low",
            {"harm_type": "Model theft"},
        ),
        (
            {
                "severity": "Sev 2.5",
                "severity_other": "Sev 2.5",
                "harm_type": None,
                "harm_type_other": None,
                "description": "Notes",
                "field_values": {"steps": "1. Open", "description": "Notes"},
            },
            "Sev 2.5",
            {"steps": "1. Open", "description": "Notes"},
        ),
    ],
)
def test_finding_create_moves_deprecated_fields(
    old_fields: dict[str, object], severity: str, field_values: dict[str, str]
) -> None:
    with pytest.warns(DeprecationWarning, match=r"FindingCreate\(\w+=\.\.\.\) is deprecated .* 1\.5\.0"):
        finding = FindingCreate.model_validate({"title": "Leak", **old_fields})
    assert finding.severity == severity
    assert finding.field_values == field_values


@pytest.mark.parametrize(
    ("old_fields", "message"),
    [
        ({"severity": "other", "description": "Notes"}, "severity_other must not be blank when severity is Other"),
        ({"severity": "other", "severity_other": " "}, "severity_other must not be blank when severity is Other"),
        ({"severity": "low", "severity_other": "Sev 2.5"}, "severity_other is only allowed when severity is Other"),
        ({"severity": "low", "harm_type": "Other"}, "harm_type_other must not be blank when harm_type is Other"),
        ({"severity": "low", "harm_type_other": "Theft"}, "harm_type_other is only allowed when harm_type is Other"),
        (
            {"severity": "low", "description": "Notes", "field_values": {"description": "Other notes"}},
            r"description conflicts with field_values\['description'\]",
        ),
    ],
)
def test_finding_create_rejects_invalid_deprecated_fields(old_fields: dict[str, object], message: str) -> None:
    with pytest.warns(DeprecationWarning), pytest.raises(ValidationError, match=message):
        FindingCreate.model_validate({"title": "Leak", **old_fields})


def test_finding_create_requires_text_for_the_deprecated_other_severity() -> None:
    with pytest.warns(DeprecationWarning):
        severity = importlib.import_module("pyrit.models").FindingSeverity
    with pytest.raises(ValidationError, match="severity_other must not be blank when severity is Other"):
        FindingCreate(title="Leak", severity=severity.OTHER)
    # A deployment's own option key "other" is not the PyRIT 1.2 severity.
    assert FindingCreate(title="Leak", severity="other").severity == "other"


@pytest.mark.parametrize(
    ("severity", "field_values", "expected"),
    [
        (
            "critical",
            {"harm_type": "Malware", "description": "Notes"},
            {
                "description": "Notes",
                "severity_other": None,
                "harm_type": HarmCategory.MALWARE,
                "harm_type_other": None,
            },
        ),
        (
            "Sev 2.5",
            {"harm_type": "Model theft"},
            {
                "description": "",
                "severity_other": "Sev 2.5",
                "harm_type": HarmCategory.OTHER,
                "harm_type_other": "Model theft",
            },
        ),
        ("low", {}, {"description": "", "severity_other": None, "harm_type": None, "harm_type_other": None}),
    ],
)
def test_finding_reads_deprecated_fields(
    severity: str, field_values: dict[str, str], expected: dict[str, object]
) -> None:
    finding = Finding(operation_id=OPERATION_ID, title="Leak", severity=severity, field_values=field_values)
    for name, value in expected.items():
        with pytest.warns(DeprecationWarning, match=rf"Finding\.{name} is deprecated .* 1\.5\.0"):
            assert getattr(finding, name) == value


def test_finding_severity_value_is_deprecated() -> None:
    finding = Finding(operation_id=OPERATION_ID, title="Leak", severity=" critical ")
    with pytest.warns(DeprecationWarning, match=r"Finding\.severity\.value is deprecated .* 1\.5\.0"):
        assert finding.severity.value == "critical"
    assert finding.severity == "critical"
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
