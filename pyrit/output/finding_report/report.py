# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""An Operation's findings laid out for a report, and the text every report format shares."""

import re
from dataclasses import dataclass
from enum import Enum

EVIDENCE_NOTE = "Evidence is listed as references with links to the PyRIT dashboard; transcripts are not included."
UNAVAILABLE_EVIDENCE = "Evidence unavailable"
DETAIL_SEPARATOR = " · "
PATHS_LABEL = "Paths"
PATH_STEP_SEPARATOR = " → "
STEP_NOT_DEMONSTRATED = "not demonstrated"

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
NOT_DEMONSTRATED = FindingReportField(label="Status", value="Not demonstrated", column=FindingReportColumn.ASSESSMENT)


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
class FindingReportStep:
    """A step of a path: a sub-finding's number and title, and whether it was demonstrated."""

    number: str
    title: str
    demonstrated: bool

    @property
    def label(self) -> str:
        """The sub-finding's numbered title."""
        return f"{self.number} {self.title}"


@dataclass(frozen=True)
class FindingReportPath:
    """One ordered chain of a finding's sub-findings that together demonstrate it."""

    name: str
    steps: tuple[FindingReportStep, ...]

    @property
    def complete(self) -> bool:
        """Whether every step was demonstrated. A partial path shows which steps are missing."""
        return all(step.demonstrated for step in self.steps)

    @property
    def status(self) -> str:
        """The path's completeness, as the report shows it."""
        return "complete" if self.complete else "partial"


@dataclass(frozen=True)
class FindingReportEntry:
    """
    A finding's title, severity, other populated fields in report order, evidence, paths, and sub-findings.

    ``number`` places the finding in the report, for example ``1.2`` for the second sub-finding of the first
    finding, so path steps can refer to sub-findings.
    """

    title: str
    severity: FindingReportField
    fields: tuple[FindingReportField, ...]
    evidence: tuple[FindingReportEvidence, ...]
    number: str = ""
    demonstrated: bool = True
    paths: tuple[FindingReportPath, ...] = ()
    children: tuple["FindingReportEntry", ...] = ()

    @property
    def heading(self) -> str:
        """The finding's numbered title."""
        return f"{self.number} {self.title}" if self.number else self.title

    @property
    def status(self) -> FindingReportField | None:
        """The finding's status, shown only when it was not demonstrated."""
        return None if self.demonstrated else NOT_DEMONSTRATED


@dataclass(frozen=True)
class FindingReport:
    """An Operation's selected findings, in report order. Sub-findings are nested in their parents."""

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
