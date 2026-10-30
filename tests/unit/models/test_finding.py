# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import UUID

import pytest
from pydantic import ValidationError

from pyrit.models.finding import Finding, FindingCreate

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
