# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""An Operation's findings laid out for a report, and the text every report format shares."""

import re
from dataclasses import dataclass
from enum import Enum

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


def plain_text(value: str) -> str:
    """
    Break lines at the same characters in every format, and mark each control character visibly rather than drop it.

    Args:
        value (str): Authored text.

    Returns:
        str: The text with ``\\n`` line breaks and U+FFFD in place of each control character.
    """
    return _UNPRINTABLE.sub("\ufffd", _LINE_BREAK.sub("\n", value))
