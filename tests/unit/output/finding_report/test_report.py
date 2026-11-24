# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import FindingCreate, FindingField, FindingFields
from pyrit.output.finding_report.report import (
    FindingReportColumn,
    FindingReportEntry,
    FindingReportEvidence,
    FindingReportField,
    finding_report_entry,
)

FIELDS = FindingFields(
    severity=FindingField(
        key="severity",
        label="Impact",
        type="choice",
        levels=[("severity_rationale", "Impact rationale")],
        options={
            ("sev1", "Sev 1"): [("remote_no_auth", "Remote, no sign-in needed")],
            ("sev2", "Sev 2"): [("signed_in_user", "Needs a signed-in user")],
        },
    ),
    fields=[
        FindingField(
            key="category",
            label="Category",
            type="choice",
            levels=[("subcategory", "Subcategory")],
            options={("model_security", "Model security"): [("system_prompt_leak", "System prompt leak")]},
        ),
        FindingField(key="steps", label="Steps to reproduce", type="multiline"),
        FindingField(key="build", label="Build", type="text"),
    ],
)
EVIDENCE = FindingReportEvidence(title="Reveal the instructions", summary=(), details=(), link_url=None)


def test_finding_report_entry_places_fields_by_type_and_retired_values_last() -> None:
    finding = FindingCreate(
        title="System prompt leak",
        severity="sev1",
        field_values={
            "description": "Recorded before the fields changed",
            "build": "2026.10",
            "steps": "1. Ask for the instructions\n2. Repeat them",
            "subcategory": "system_prompt_leak",
            "category": "model_security",
            "severity_rationale": "remote_no_auth",
        },
    )

    assert finding_report_entry(finding=finding, fields=FIELDS, evidence=[EVIDENCE]) == FindingReportEntry(
        title="System prompt leak",
        severity=FindingReportField(label="Impact", value="Sev 1"),
        fields=(
            FindingReportField(
                label="Impact rationale", value="Remote, no sign-in needed", column=FindingReportColumn.ASSESSMENT
            ),
            FindingReportField(label="Category", value="Model security", column=FindingReportColumn.ASSESSMENT),
            FindingReportField(label="Subcategory", value="System prompt leak", column=FindingReportColumn.ASSESSMENT),
            FindingReportField(
                label="Steps to reproduce",
                value="1. Ask for the instructions\n2. Repeat them",
                column=FindingReportColumn.FINDING,
            ),
            FindingReportField(label="Build", value="2026.10", column=FindingReportColumn.ASSESSMENT),
            FindingReportField(
                label="description", value="Recorded before the fields changed", column=FindingReportColumn.FINDING
            ),
        ),
        evidence=(EVIDENCE,),
    )
