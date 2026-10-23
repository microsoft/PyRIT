# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Selected findings of an Operation as a Word or Markdown section to copy into a report."""

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Literal
from urllib.parse import quote, urlencode
from uuid import UUID

import regex
from docx import Document
from docx.document import Document as DocxDocument
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import _Cell
from docx.text.paragraph import Paragraph
from pydantic import AnyHttpUrl

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.operations import (
    EvidenceAvailability,
    FindingEvidenceItem,
    FindingExportFormat,
    FindingExportRequest,
)
from pyrit.backend.services.operation_service import FindingNotFoundError, OperationService
from pyrit.models import Finding, FindingSeverity, ScoreStatus
from pyrit.models.harm_category import HarmCategory

_PAGE_SIZE = 100
_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EVIDENCE_NOTE = "Evidence is listed as references with links to the PyRIT dashboard; transcripts are not included."
_UNAVAILABLE = "Evidence unavailable"
_LINK_COLOR = RGBColor(0x05, 0x63, 0xC1)
_HUMAN_SCORER = "ManualScorer"
_BOOLEAN_SCORES = {"true": "True", "false": "False"}
# Word 2013 and later; the template's older mode opens documents in Compatibility Mode.
_WORD_COMPATIBILITY_MODE = "15"
_WORD_BODY_FONT = "Calibri"
_WORD_HEADING_FONT = "Calibri Light"
_WORD_TITLE_COLOR = RGBColor(0x2F, 0x54, 0x96)
_WORD_TITLE_SIZE = Pt(16)
_WORD_TABLE_STYLE = "Table Grid"
_WORD_COLUMNS = ("Finding", "Assessment")
_DETAIL_SEPARATOR = " · "
_WORD_BULLET_STYLE = "List Bullet"
# The template's List Bullet text indent, so an entry's lines align under its bullet text.
_WORD_BULLET_TEXT_INDENT = Pt(18)
_THEME_COLOR_ATTRIBUTES = ("w:themeColor", "w:themeShade", "w:themeTint")
_LINE_BREAK = re.compile("\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]")
_UNPRINTABLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")
_MARKDOWN_SPECIAL = re.compile(r"[\\`*_\[\]<>&#|~$]")
_MARKDOWN_DESTINATION_UNSAFE = re.compile(r"[<>\s]")
_FILENAME_SEPARATORS = regex.compile(r"[^\p{L}\p{M}\p{N}]+")
_COMBINING_MARK = regex.compile(r"\p{M}")
_FILENAME_NAME_LIMIT = 60


@dataclass(frozen=True)
class FindingExportFile:
    """A generated findings document and the name to download it as."""

    content: bytes
    media_type: str
    filename: str


@dataclass(frozen=True)
class _ReportField:
    label: str
    value: str
    column: Literal["finding", "assessment"] = "finding"


_NO_EVIDENCE = _ReportField(label="Evidence", value="none attached")


@dataclass(frozen=True)
class _ReportEvidence:
    title: str
    summary: tuple[_ReportField, ...]
    details: tuple[_ReportField, ...]
    link_url: str | None

    @property
    def unavailable(self) -> bool:
        return self.link_url is None


@dataclass(frozen=True)
class _ReportFinding:
    title: str
    severity: _ReportField
    fields: tuple[_ReportField, ...]
    evidence: tuple[_ReportEvidence, ...]


@dataclass(frozen=True)
class _FindingReport:
    operation_name: str
    findings: tuple[_ReportFinding, ...]


async def export_findings_async(*, operation_id: UUID, request: FindingExportRequest) -> FindingExportFile:
    """
    Build one document of an Operation's selected findings, in the dashboard's listing order, with evidence references.

    Evidence appears as saved metadata and dashboard links, without transcripts or media.

    Returns:
        FindingExportFile: The complete document.

    Raises:
        OperationNotFoundError: If the Operation does not exist.
        FindingNotFoundError: If a selected finding is not in the Operation.
    """
    report = await _build_report_async(operation_id=operation_id, request=request)
    now = datetime.now(UTC)
    if request.format == FindingExportFormat.MARKDOWN:
        extension, media_type = "md", _MARKDOWN_MEDIA_TYPE
        content = _render_markdown(report).encode("utf-8")
    else:
        extension, media_type = "docx", _DOCX_MEDIA_TYPE
        content = await asyncio.to_thread(_render_docx, report, now=now)
    filename = _export_filename(
        operation_id=operation_id, operation_name=report.operation_name, extension=extension, now=now
    )
    return FindingExportFile(content=content, media_type=media_type, filename=filename)


def _export_filename(*, operation_id: UUID, operation_name: str, extension: str, now: datetime) -> str:
    """
    Name a download after its Operation, with the time in labelled UTC.

    Browsers save under the dashboard's own name, which uses the user's local time instead.

    Returns:
        str: For example ``copyrit-findings-synthetic-evaluation-2026-10-22-1658-utc.docx``.
    """
    name = _FILENAME_SEPARATORS.sub("-", unicodedata.normalize("NFC", operation_name).lower()).strip("-")
    if len(name) > _FILENAME_NAME_LIMIT:
        end = _FILENAME_NAME_LIMIT
        if name[end] != "-" and "-" in name[:end]:
            end = name.rindex("-", 0, end)
        # A word longer than the limit is cut between letters, never between a letter and its marks.
        while end > 0 and _COMBINING_MARK.match(name[end]):
            end -= 1
        name = name[:end].rstrip("-")
    return f"copyrit-findings-{name or operation_id}-{now.astimezone(UTC):%Y-%m-%d-%H%M}-utc.{extension}"


async def _build_report_async(*, operation_id: UUID, request: FindingExportRequest) -> _FindingReport:
    service = OperationService()
    operation = await service.get_async(operation_id)
    findings = await _load_selected_findings_async(
        service=service, operation_id=operation_id, finding_ids=request.finding_ids
    )
    report_findings = []
    for finding in findings:
        evidence = await _load_evidence_async(service=service, finding=finding)
        severity, fields = _project_finding_fields(finding)
        report_findings.append(
            _ReportFinding(
                title=finding.title,
                severity=severity,
                fields=fields,
                evidence=tuple(
                    _project_evidence(
                        item=item, operation_id=operation_id, dashboard_base_url=request.dashboard_base_url
                    )
                    for item in evidence
                ),
            )
        )
    return _FindingReport(operation_name=operation.name, findings=tuple(report_findings))


async def _load_selected_findings_async(
    *, service: OperationService, operation_id: UUID, finding_ids: list[UUID]
) -> list[Finding]:
    """
    Read the selected findings in the order ``OperationService.list_findings_async`` lists them for the dashboard.

    Returns:
        list[Finding]: Every selected finding.

    Raises:
        OperationNotFoundError: If the Operation does not exist.
        FindingNotFoundError: If a selected finding is not in the Operation.
    """
    wanted = set(finding_ids)
    selected: dict[UUID, Finding] = {}
    offset: int | None = 0
    while offset is not None and len(selected) < len(wanted):
        page = await service.list_findings_async(operation_id=operation_id, limit=_PAGE_SIZE, offset=offset)
        for finding in page.items:
            # A concurrent change can shift a row onto the next page, so keep its first read.
            if finding.id in wanted:
                selected.setdefault(finding.id, finding)
        offset = page.next_offset
    missing = next((finding_id for finding_id in finding_ids if finding_id not in selected), None)
    if missing is not None:
        raise FindingNotFoundError(f"Finding '{missing}' not found in operation '{operation_id}'.")
    return list(selected.values())


async def _load_evidence_async(*, service: OperationService, finding: Finding) -> list[FindingEvidenceItem]:
    items: dict[UUID, FindingEvidenceItem] = {}
    offset: int | None = 0
    while offset is not None:
        page = await service.list_finding_evidence_async(
            operation_id=finding.operation_id, finding_id=finding.id, limit=_PAGE_SIZE, offset=offset
        )
        for item in page.items:
            items.setdefault(item.item.id, item)
        offset = page.next_offset
    return list(items.values())


def _project_finding_fields(finding: Finding) -> tuple[_ReportField, tuple[_ReportField, ...]]:
    """
    Label a finding's severity and its populated fields, in report order, and place each in a Word column.

    Narrative text goes in the Finding column and classifications in the Assessment column.

    Returns:
        tuple[_ReportField, tuple[_ReportField, ...]]: The severity, then the populated fields.
    """
    severity = (
        finding.severity_other or "Other"
        if finding.severity == FindingSeverity.OTHER
        else finding.severity.value.capitalize()
    )
    harm_type = finding.harm_type_other if finding.harm_type == HarmCategory.OTHER else finding.harm_type
    fields = [
        _ReportField(label="Harm-type", value=str(harm_type), column="assessment") if harm_type else None,
        _ReportField(label="Description", value=finding.description) if finding.description else None,
    ]
    return _ReportField(label="Severity", value=severity), tuple(field for field in fields if field)


def _project_evidence(
    *, item: FindingEvidenceItem, operation_id: UUID, dashboard_base_url: AnyHttpUrl
) -> _ReportEvidence:
    if item.outcome is None:
        # The attack result is gone, so its saved ID stands in for its objective.
        return _ReportEvidence(title=str(item.item.attack_result_id), summary=(), details=(), link_url=None)
    objective = item.objective if item.objective and item.objective.strip() else "No objective"
    summary = (
        _ReportField(label="Outcome", value=item.outcome.value.capitalize()),
        _ReportField(label="Score", value=_score_text(item.score)),
    )
    details = []
    if item.score is not None and item.score.score_rationale:
        details.append(_ReportField(label="Rationale", value=item.score.score_rationale))
    # As on the dashboard, an attack's Operation is named only when it isn't the document's own.
    if item.source_operation_id != operation_id:
        details.append(_ReportField(label="From operation", value=item.source_operation_name or "Not in an operation"))
    link_url = None
    if item.availability == EvidenceAvailability.AVAILABLE and item.conversation_id is not None:
        link_url = _conversation_link(
            dashboard_base_url=dashboard_base_url, item=item, conversation_id=item.conversation_id
        )
    return _ReportEvidence(title=objective, summary=summary, details=tuple(details), link_url=link_url)


def _score_text(score: ScoreView | None) -> str:
    if score is None:
        return "Not scored"
    if score.status == ScoreStatus.UNDETERMINED:
        value = "Undetermined"
    else:
        value = _BOOLEAN_SCORES.get(str(score.score_value).lower(), str(score.score_value))
    scorer = "human review" if score.scorer_type == _HUMAN_SCORER else score.scorer_type
    return f"{value} ({scorer})"


def _conversation_link(*, dashboard_base_url: AnyHttpUrl, item: FindingEvidenceItem, conversation_id: str) -> str:
    """
    Build the dashboard route that opens evidence's conversation and returns to the evidence.

    Returns:
        str: The absolute link, matching the dashboard's ``attackConversationRoutePath``.
    """
    path = (
        f"/attacks/{_encode_segment(str(item.item.attack_result_id))}/conversations/{_encode_segment(conversation_id)}"
    )
    query = {"scenarioResultId": str(item.scenario_result_id)} if item.scenario_result_id else {}
    query["findingEvidenceId"] = str(item.item.id)
    return f"{str(dashboard_base_url).rstrip('/')}{path}?{urlencode(query)}"


def _encode_segment(value: str) -> str:
    # The same characters JavaScript's encodeURIComponent leaves unescaped.
    return quote(value, safe="!*'()")


def _render_docx(report: _FindingReport, *, now: datetime) -> bytes:
    document = Document()
    title = _plain_text(f"Findings - {report.operation_name}")
    _replace_template_metadata(document=document, title=title, now=now)
    _replace_theme_formatting(document)
    _use_current_word_spacing(document)
    document.add_heading(title, level=1)
    document.add_paragraph(_EVIDENCE_NOTE)
    table = document.add_table(rows=1, cols=len(_WORD_COLUMNS))
    table.style = document.styles[_WORD_TABLE_STYLE]
    header = table.rows[0]
    for cell, column in zip(header.cells, _WORD_COLUMNS, strict=True):
        cell.paragraphs[0].add_run(column).bold = True
    for finding in report.findings:
        finding_cell, assessment_cell = table.add_row().cells
        finding_cell.paragraphs[0].add_run(_plain_text(finding.title)).bold = True
        for field in finding.fields:
            if field.column == "finding":
                _write_word_field(paragraph=finding_cell.add_paragraph(), field=field)
        _add_word_evidence(cell=finding_cell, evidence=finding.evidence)
        _write_word_field(paragraph=assessment_cell.paragraphs[0], field=finding.severity)
        for field in finding.fields:
            if field.column == "assessment":
                _write_word_field(paragraph=assessment_cell.add_paragraph(), field=field)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _replace_template_metadata(*, document: DocxDocument, title: str, now: datetime) -> None:
    """Describe the export rather than python-docx's template, and open it outside Word's Compatibility Mode."""
    for setting in document.settings.element.iter(qn("w:compatSetting")):
        if setting.get(qn("w:name")) == "compatibilityMode":
            setting.set(qn("w:val"), _WORD_COMPATIBILITY_MODE)
    properties = document.core_properties
    properties.title = title
    properties.author = properties.comments = properties.last_modified_by = ""
    properties.created = properties.modified = now.astimezone(UTC)
    properties.revision = 1
    # The template's sample preview image and "Microsoft Macintosh Word" application details don't describe this file.
    package_relationships = document.part.package.rels
    for relationship_id, relationship in list(package_relationships.items()):
        if relationship.reltype in (RELATIONSHIP_TYPE.EXTENDED_PROPERTIES, RELATIONSHIP_TYPE.THUMBNAIL):
            del package_relationships[relationship_id]


def _replace_theme_formatting(document: DocxDocument) -> None:
    """Name Latin fonts and text colors explicitly rather than through the template's 2007-era theme."""
    styles = document.styles.element
    for fonts in styles.iter(qn("w:rFonts")):
        theme = fonts.get(qn("w:asciiTheme"))
        if theme is None:
            continue
        family = _WORD_HEADING_FONT if theme.startswith("major") else _WORD_BODY_FONT
        for attribute in ("w:asciiTheme", "w:hAnsiTheme"):
            fonts.attrib.pop(qn(attribute), None)
        fonts.set(qn("w:ascii"), family)
        fonts.set(qn("w:hAnsi"), family)
    for color in styles.iter(qn("w:color")):
        for attribute in _THEME_COLOR_ATTRIBUTES:
            color.attrib.pop(qn(attribute), None)


def _use_current_word_spacing(document: DocxDocument) -> None:
    """Give body text and the title the spacing and look of a new Word document."""
    normal = document.styles["Normal"].paragraph_format
    normal.space_after = Pt(8)
    normal.line_spacing = 1.08
    title = document.styles["Heading 1"]
    title.font.color.rgb = _WORD_TITLE_COLOR
    title.font.size = _WORD_TITLE_SIZE
    title.font.bold = False
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(4)


def _add_word_evidence(*, cell: _Cell, evidence: tuple[_ReportEvidence, ...]) -> None:
    if not evidence:
        _write_word_field(paragraph=cell.add_paragraph(), field=_NO_EVIDENCE)
        return
    cell.add_paragraph().add_run(f"{_NO_EVIDENCE.label}:").bold = True
    for entry in evidence:
        lead = cell.add_paragraph(style=_WORD_BULLET_STYLE)
        lead.paragraph_format.space_before = Pt(6)
        lead.paragraph_format.space_after = Pt(2)
        if entry.link_url is not None:
            _add_word_hyperlink(paragraph=lead, url=entry.link_url, text=_plain_text(entry.title))
        else:
            lead.add_run(_plain_text(entry.title)).bold = True
        if entry.summary:
            line = _indented_paragraph(cell)
            for index, field in enumerate(entry.summary):
                if index:
                    line.add_run(_DETAIL_SEPARATOR)
                _write_word_field(paragraph=line, field=field)
        for field in entry.details:
            _write_word_field(paragraph=_indented_paragraph(cell), field=field)
        if entry.unavailable:
            _indented_paragraph(cell).add_run(_UNAVAILABLE)


def _indented_paragraph(cell: _Cell) -> Paragraph:
    paragraph: Paragraph = cell.add_paragraph()
    paragraph.paragraph_format.left_indent = _WORD_BULLET_TEXT_INDENT
    paragraph.paragraph_format.space_after = Pt(2)
    return paragraph


def _write_word_field(*, paragraph: Paragraph, field: _ReportField) -> None:
    paragraph.add_run(f"{_plain_text(field.label)}: ").bold = True
    paragraph.add_run(_plain_text(field.value))


def _add_word_hyperlink(*, paragraph: Paragraph, url: str, text: str) -> None:
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    run = paragraph.add_run(text)
    run.font.underline = True
    run.font.color.rgb = _LINK_COLOR
    hyperlink.append(run._r)
    paragraph._p.append(hyperlink)


def _plain_text(value: str) -> str:
    # Both formats break lines at the same characters, and mark each control character visibly rather than drop it.
    return _UNPRINTABLE.sub("\ufffd", _LINE_BREAK.sub("\n", value))


def _render_markdown(report: _FindingReport) -> str:
    """
    Lay the report out like the Word table, read top to bottom.

    Each finding lists its Assessment fields, then its Finding fields, then its evidence as bullets.

    Returns:
        str: The Markdown document.
    """
    blocks = [f"# Findings - {_markdown_inline(report.operation_name)}", _EVIDENCE_NOTE]
    for finding in report.findings:
        blocks.append(f"## {_markdown_inline(finding.title)}")
        assessment = [field for field in finding.fields if field.column == "assessment"]
        account = [field for field in finding.fields if field.column == "finding"]
        blocks.extend(_markdown_field(field) for field in (finding.severity, *assessment, *account))
        blocks.append(_markdown_evidence(finding.evidence))
    return "\n\n".join(blocks) + "\n"


def _markdown_evidence(evidence: tuple[_ReportEvidence, ...]) -> str:
    if not evidence:
        return _markdown_field(_NO_EVIDENCE)
    lines = [f"**{_NO_EVIDENCE.label}:**", ""]
    for entry in evidence:
        title = _markdown_inline(entry.title)
        if entry.link_url is not None:
            lines.append(f"- [{title}](<{_MARKDOWN_DESTINATION_UNSAFE.sub(_percent_encode, entry.link_url)}>)")
        else:
            lines.append(f"- **{title}**")
        items = []
        if entry.summary and all(_is_short(field) for field in entry.summary):
            items.append(_DETAIL_SEPARATOR.join(_markdown_field(field) for field in entry.summary))
        else:
            items.extend(_markdown_field(field) for field in entry.summary)
        items.extend(_markdown_field(field) for field in entry.details)
        if entry.unavailable:
            items.append(_UNAVAILABLE)
        lines.extend(_markdown_sub_item(item) for item in items)
    return "\n".join(lines)


def _markdown_sub_item(text: str) -> str:
    # Continuation lines, such as a fenced block's, are indented to the sub-item's content so they stay inside it.
    first, *rest = text.split("\n")
    return "\n".join([f"  - {first}", *(f"    {line}" if line else "" for line in rest)])


def _is_short(field: _ReportField) -> bool:
    return "\n" not in _plain_text(field.value)


def _markdown_field(field: _ReportField) -> str:
    if _is_short(field):
        return f"**{_markdown_inline(field.label)}:** {_markdown_inline(field.value)}"
    return f"**{_markdown_inline(field.label)}**\n\n{_markdown_literal(field.value)}"


def _markdown_inline(value: str) -> str:
    """
    Make text safe to place after other text on a Markdown line.

    Returns:
        str: The text on one line, with Markdown and HTML syntax characters escaped.
    """
    return _MARKDOWN_SPECIAL.sub(r"\\\g<0>", re.sub(r"\n+", " ", _plain_text(value)))


def _markdown_literal(value: str) -> str:
    """
    Fence text as a literal block that no line of the text can close.

    Returns:
        str: The fenced block.
    """
    text = _plain_text(value)
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}text\n{text}\n{fence}"


def _percent_encode(match: re.Match[str]) -> str:
    return quote(match.group(0), safe="")
