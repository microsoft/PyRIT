# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""An Operation's findings laid out for a report, and the text every report format shares."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from pyrit.models import DecompositionNode, DecompositionPath, DecompositionTree, Finding, FindingSeverity
from pyrit.models.harm_category import HarmCategory

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
    description: str = ""

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


def build_finding_report(
    *,
    operation_name: str,
    findings: Sequence[Finding],
    evidence: Mapping[UUID, tuple[FindingReportEvidence, ...]],
    part_of: Mapping[UUID, str],
) -> FindingReport:
    """
    Build a numbered report, preserving root order and sorting children oldest first.

    Args:
        operation_name (str): The report's operation name.
        findings (Sequence[Finding]): Complete selected subtrees, without repeated findings.
        evidence (Mapping[UUID, tuple[FindingReportEvidence, ...]]): Projected evidence for each finding.
        part_of (Mapping[UUID, str]): Titles of parents outside the selected subtrees.

    Returns:
        FindingReport: The report, ready for a formatter without further data access.
    """
    by_id = {finding.id: finding for finding in findings}
    roots = [finding for finding in findings if finding.parent_finding_id not in by_id]
    children = sorted(
        (finding for finding in findings if finding.parent_finding_id in by_id),
        key=lambda finding: (finding.created_at, finding.id),
    )
    tree = DecompositionTree(
        DecompositionNode(
            id=finding.id,
            parent_id=finding.parent_finding_id if finding.parent_finding_id in by_id else None,
            paths=finding.paths,
        )
        for finding in (*roots, *children)
    )
    return FindingReport(
        operation_name=operation_name,
        findings=_project_entries(tree=tree, findings=by_id, evidence=evidence, part_of=part_of),
    )


def _project_entries(
    *,
    tree: DecompositionTree,
    findings: Mapping[UUID, Finding],
    evidence: Mapping[UUID, tuple[FindingReportEvidence, ...]],
    part_of: Mapping[UUID, str],
) -> tuple[FindingReportEntry, ...]:
    numbers = {root: str(index) for index, root in enumerate(tree.roots, start=1)}
    visit_order = [finding_id for root in tree.roots for finding_id, _ in tree.walk(root)]
    for finding_id in visit_order:
        for index, child in enumerate(tree.get_children(finding_id), start=1):
            numbers[child] = f"{numbers[finding_id]}.{index}"
    entries: dict[UUID, FindingReportEntry] = {}
    roots = set(tree.roots)
    for finding_id in reversed(visit_order):
        finding = findings[finding_id]
        parent_id = finding.parent_finding_id
        severity, fields = _project_finding_fields(
            finding=finding, part_of=part_of.get(parent_id) if parent_id is not None and finding_id in roots else None
        )
        entries[finding_id] = FindingReportEntry(
            title=finding.title,
            severity=severity,
            fields=fields,
            evidence=evidence[finding_id],
            number=numbers[finding_id],
            demonstrated=finding.demonstrated,
            paths=tuple(
                _project_path(index=index, path=path, findings=findings, numbers=numbers)
                for index, path in enumerate(finding.paths, start=1)
            ),
            children=tuple(entries[child] for child in tree.get_children(finding_id)),
        )
    return tuple(entries[root] for root in tree.roots)


def _project_path(
    *, index: int, path: DecompositionPath, findings: Mapping[UUID, Finding], numbers: Mapping[UUID, str]
) -> FindingReportPath:
    return FindingReportPath(
        name=path.name if path.name and path.name.strip() else f"Path {index}",
        description=path.description,
        steps=tuple(
            FindingReportStep(
                number=numbers[step], title=findings[step].title, demonstrated=findings[step].demonstrated
            )
            for step in path.steps
        ),
    )


def _project_finding_fields(
    *, finding: Finding, part_of: str | None
) -> tuple[FindingReportField, tuple[FindingReportField, ...]]:
    severity = (
        finding.severity_other or "Other"
        if finding.severity == FindingSeverity.OTHER
        else finding.severity.value.capitalize()
    )
    harm_type = finding.harm_type_other if finding.harm_type == HarmCategory.OTHER else finding.harm_type
    fields = [
        FindingReportField(label="Harm-type", value=str(harm_type), column=FindingReportColumn.ASSESSMENT)
        if harm_type
        else None,
        FindingReportField(label="Part of", value=part_of) if part_of else None,
        FindingReportField(label="Description", value=finding.description) if finding.description else None,
    ]
    return FindingReportField(label="Severity", value=severity), tuple(field for field in fields if field)


def plain_text(value: str) -> str:
    """
    Break lines at the same characters in every format, and mark each control character visibly rather than drop it.

    Args:
        value (str): Authored text.

    Returns:
        str: The text with ``\\n`` line breaks and U+FFFD in place of each control character.
    """
    return _UNPRINTABLE.sub("\ufffd", _LINE_BREAK.sub("\n", value))
