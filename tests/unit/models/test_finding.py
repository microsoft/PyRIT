# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import UUID

import pytest
from pydantic import ValidationError

from pyrit.models.finding import Finding, FindingCreate
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
