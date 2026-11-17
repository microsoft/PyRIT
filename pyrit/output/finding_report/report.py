# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""An Operation's findings laid out for a report, and the text every report format shares."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from pyrit.models import FindingCreate, FindingFields

EVIDENCE_NOTE = "Evidence is listed as references with links to the PyRIT dashboard; transcripts are not included."
UNAVAILABLE_EVIDENCE = "Evidence unavailable"
DETAIL_SEPARATOR = " · "

_LINE_BREAK = re.compile("\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]")
_UNPRINTABLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")


class FindingReportColumn(str, Enum):
    """The column of a two-column report that a field belongs in."""

    FINDING = "finding"
    ASSESSMENT = "assessment"


@dataclass(frozen=True)
class FindingReportField:
    """A labelled value, such as a finding's severity or an evidence item's score."""

    label: str
    value: str
    column: FindingReportColumn = FindingReportColumn.FINDING


NO_EVIDENCE = FindingReportField(label="Evidence", value="none attached")


@dataclass(frozen=True)
class FindingReportEvidence:
    """
    An attack result attached to a finding, as a reference rather than a transcript.

    The ``summary`` fields share one line, and each of the ``details`` has its own.
    """

    title: str
    summary: tuple[FindingReportField, ...]
    details: tuple[FindingReportField, ...]
    link_url: str | None

    @property
    def unavailable(self) -> bool:
        """Whether the evidence has no conversation to link to."""
        return self.link_url is None


@dataclass(frozen=True)
class FindingReportEntry:
    """A finding's title, severity, other populated fields in report order, and evidence."""

    title: str
    severity: FindingReportField
    fields: tuple[FindingReportField, ...]
    evidence: tuple[FindingReportEvidence, ...]


@dataclass(frozen=True)
class FindingReport:
    """An Operation's selected findings, in report order."""

    operation_name: str
    findings: tuple[FindingReportEntry, ...]


def finding_report_entry(
    *, finding: FindingCreate, fields: FindingFields, evidence: Sequence[FindingReportEvidence] = ()
) -> FindingReportEntry:
    """
    Lay out a finding for a report, labelled by finding fields.

    Multi-line fields go in the Finding column; choices and single-line text go in the Assessment column. Values that
    no configured field uses follow in the Finding column, labelled by their stored keys.

    Args:
        finding (FindingCreate): The finding's title, severity and field values.
        fields (FindingFields): The finding fields that label the values.
        evidence (Sequence[FindingReportEvidence]): The finding's evidence references. Defaults to none.

    Returns:
        FindingReportEntry: The finding's severity, its filled fields in configured order, its retired values, and
            its evidence.
    """
    description = fields.describe_values(severity=finding.severity, field_values=finding.field_values)
    current = (
        FindingReportField(
            label=row.label,
            value=row.value,
            column=FindingReportColumn.FINDING if row.multiline else FindingReportColumn.ASSESSMENT,
        )
        for row in description.rows
    )
    retired = (FindingReportField(label=key, value=value) for key, value in description.retired)
    return FindingReportEntry(
        title=finding.title,
        severity=FindingReportField(label=fields.severity.label, value=description.severity_label),
        fields=(*current, *retired),
        evidence=tuple(evidence),
    )


def plain_text(value: str) -> str:
    """
    Break lines at the same characters in every format, and mark each control character visibly rather than drop it.

    Args:
        value (str): Authored text.

    Returns:
        str: The text with ``\\n`` line breaks and U+FFFD in place of each control character.
    """
    return _UNPRINTABLE.sub("\ufffd", _LINE_BREAK.sub("\n", value))
