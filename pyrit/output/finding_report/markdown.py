# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from urllib.parse import quote

from pyrit.output.base import PrinterBase
from pyrit.output.finding_report.report import (
    DETAIL_SEPARATOR,
    EVIDENCE_NOTE,
    NO_EVIDENCE,
    UNAVAILABLE_EVIDENCE,
    FindingReport,
    FindingReportColumn,
    FindingReportEvidence,
    FindingReportField,
    plain_text,
)
from pyrit.output.sink import Sink

_MARKDOWN_SPECIAL = re.compile(r"[\\`*_\[\]<>&#|~$]")
# List item markers and heading underlines, which only take effect at the start of a line.
_MARKDOWN_BLOCK_MARKER = re.compile(r"^([0-9]+(?=[.)])|(?=[-+=]))")
_MARKDOWN_DESTINATION_UNSAFE = re.compile(r"[<>\s]")


class MarkdownFindingReportPrinter(PrinterBase):
    """
    Markdown printer for a report of an Operation's findings.

    Lays the report out like a two-column table read top to bottom, and keeps authored text as escaped prose.
    """

    def __init__(self, *, sink: Sink | None = None) -> None:
        """
        Initialize the markdown finding report printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
        """
        super().__init__(sink=sink)

    async def render_async(self, report: FindingReport) -> str:
        """
        Render the report as a Markdown document.

        Each finding lists its severity and Assessment fields, then its Finding fields, then its evidence as bullets.

        Args:
            report (FindingReport): The findings to render.

        Returns:
            str: The Markdown document.
        """
        blocks = [f"# Findings - {_markdown_inline(report.operation_name)}", EVIDENCE_NOTE]
        for finding in report.findings:
            blocks.append(f"## {_markdown_inline(finding.title)}")
            assessment = [field for field in finding.fields if field.column == FindingReportColumn.ASSESSMENT]
            account = [field for field in finding.fields if field.column == FindingReportColumn.FINDING]
            blocks.extend(_markdown_field(field) for field in (finding.severity, *assessment, *account))
            blocks.append(_markdown_evidence(finding.evidence))
        return "\n\n".join(blocks) + "\n"


def _markdown_evidence(evidence: tuple[FindingReportEvidence, ...]) -> str:
    if not evidence:
        return _markdown_field(NO_EVIDENCE)
    lines = [f"**{NO_EVIDENCE.label}:**", ""]
    for entry in evidence:
        title = _markdown_inline(entry.title)
        if entry.link_url is not None:
            lines.append(f"- [{title}](<{_MARKDOWN_DESTINATION_UNSAFE.sub(_percent_encode, entry.link_url)}>)")
        else:
            lines.append(f"- **{title}**")
        items = [DETAIL_SEPARATOR.join(_markdown_field(field) for field in entry.summary)] if entry.summary else []
        items.extend(_markdown_field(field) for field in entry.details)
        if entry.unavailable:
            items.append(UNAVAILABLE_EVIDENCE)
        lines.extend(_markdown_sub_item(item) for item in items)
    return "\n".join(lines)


def _markdown_sub_item(text: str) -> str:
    # Continuation lines are indented to the sub-item's content so they stay inside it.
    first, *rest = text.split("\n")
    return "\n".join([f"  - {first}", *(f"    {line}" if line else "" for line in rest)])


def _markdown_field(field: FindingReportField) -> str:
    return f"**{_markdown_inline(field.label)}:** {_markdown_prose(field.value)}"


def _markdown_inline(value: str) -> str:
    """
    Make text safe to place after other text on a Markdown line.

    Returns:
        str: The text on one line, with Markdown and HTML syntax characters escaped.
    """
    return _MARKDOWN_SPECIAL.sub(r"\\\g<0>", re.sub(r"\n+", " ", plain_text(value)))


def _markdown_prose(value: str) -> str:
    """
    Keep a labelled value's lines and paragraphs without letting any line start Markdown or HTML syntax.

    Returns:
        str: Escaped lines, joined by hard line breaks, with one blank line between paragraphs.
    """
    paragraphs: list[list[str]] = [[]]
    for line in plain_text(value).split("\n"):
        text = line.lstrip(" \t")
        if text:
            paragraphs[-1].append(_MARKDOWN_SPECIAL.sub(r"\\\g<0>", text))
        elif paragraphs[-1]:
            paragraphs.append([])
    first, *rest = "\n\n".join("\\\n".join(lines) for lines in paragraphs if lines).split("\n")
    # The first line follows the label, so only later lines can start a list or heading.
    return "\n".join([first, *(_MARKDOWN_BLOCK_MARKER.sub(r"\1\\", line) for line in rest)])


def _percent_encode(match: re.Match[str]) -> str:
    return quote(match.group(0), safe="")
