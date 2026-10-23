# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.operations import (
    EvidenceAvailability,
    FindingEvidenceItem,
    FindingExportRequest,
    FindingListResponse,
)
from pyrit.backend.services import finding_export
from pyrit.backend.services.finding_export import (
    _FindingReport,
    _project_evidence,
    _render_docx,
    _render_markdown,
    _ReportEvidence,
    _ReportField,
    _ReportFinding,
)
from pyrit.backend.services.operation_service import OperationService
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    Finding,
    FindingEvidence,
    Message,
    MessagePiece,
    Operation,
    Score,
    ScoreStatus,
)
from pyrit.models.harm_category import HarmCategory

pytestmark = pytest.mark.usefixtures("patch_central_database")

FORMATS = ["docx", "markdown"]
DASHBOARD_BASE = "https://pyrit.example.test/team(a)/"
TRANSCRIPT_MARKER = "SYNTHETIC_TRANSCRIPT_NOT_FOR_EXPORT"
MISSING_ID = UUID(int=404)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_TYPES = {"docx": DOCX_MEDIA_TYPE, "markdown": "text/markdown; charset=utf-8"}
EXTENSIONS = {"docx": "docx", "markdown": "md"}
UTC_STAMP = r"\d{4}-\d{2}-\d{2}-\d{4}-utc"
FILENAME_OPERATION_ID = UUID("c1e5502b-91f6-4d65-b182-4b028c7a75f6")
# 12:58 in UTC-4 is 16:58 UTC.
FILENAME_TIME = datetime(2026, 10, 22, 12, 58, 33, tzinfo=timezone(timedelta(hours=-4)))
HYPERLINK_RELATIONSHIP = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RELATIONSHIP_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
EVIDENCE_NOTE = "Evidence is listed as references with links to the PyRIT dashboard; transcripts are not included."
# A saved conversation ID with path, query, and fragment characters, and its encodeURIComponent form.
ODD_CONVERSATION_ID = "branch/α (1)?#%"
ENCODED_CONVERSATION_ID = "branch%2F%CE%B1%20(1)%3F%23%25"
HOSTILE_BODY = (
    "Literal ```` run\n````\n# Forged finding\n<img src=x onerror=alert(1)>\n"
    "![image](https://evil.example/i.png)\n[link](https://evil.example)\nÜnïcødé 😀 中文\n"
)
NOT_SCORED = _ReportField(label="Score", value="Not scored")
NOT_IN_AN_OPERATION = _ReportField(label="From operation", value="Not in an operation")


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _export(
    *,
    headers: dict[str, str],
    operation_id: UUID,
    finding_ids: Sequence[UUID | str],
    export_format: str | None = "docx",
):
    body: dict[str, object] = {
        "finding_ids": [str(finding_id) for finding_id in finding_ids],
        "dashboard_base_url": DASHBOARD_BASE,
    }
    if export_format is not None:
        body["format"] = export_format
    return _client().post(f"/api/operations/{operation_id}/findings/export", json=body, headers=headers)


async def _report_async(*, operation_id: UUID, finding_ids: Sequence[UUID]) -> _FindingReport:
    request = FindingExportRequest.model_validate(
        {"finding_ids": list(finding_ids), "dashboard_base_url": DASHBOARD_BASE}
    )
    return await finding_export._build_report_async(operation_id=operation_id, request=request)


def _dashboard_link(*, evidence: FindingEvidence, conversation_id: str) -> str:
    return (
        f"https://pyrit.example.test/team(a)/attacks/{evidence.attack_result_id}/conversations/{conversation_id}"
        f"?findingEvidenceId={evidence.id}"
    )


def _paragraphs(content: bytes) -> list[str]:
    """Every paragraph's text in document order, including table cells (each row's left cell, then its right)."""
    texts: list[str] = []
    for block in Document(BytesIO(content)).iter_inner_content():
        if isinstance(block, Table):
            texts.extend(paragraph.text for row in block.rows for cell in row.cells for paragraph in cell.paragraphs)
        else:
            texts.append(block.text)
    return texts


def _finding_cells(content: bytes) -> list[tuple[list[str], list[str]]]:
    """Each finding's Finding and Assessment cell paragraphs, below the table's header row."""
    table = Document(BytesIO(content)).tables[0]
    rows = [[[paragraph.text for paragraph in cell.paragraphs] for cell in row.cells] for row in table.rows[1:]]
    return [(finding, assessment) for finding, assessment in rows]


def _finding_titles(content: bytes, export_format: str) -> list[str]:
    """Finding titles in document order: Markdown headings, or the first line of each Word row."""
    if export_format == "markdown":
        return [line[3:] for line in content.decode("utf-8").split("\n") if line.startswith("## ")]
    return [finding[0] for finding, _ in _finding_cells(content)]


def _links(content: bytes, export_format: str) -> list[tuple[str, str]]:
    """Read each hyperlink's text and target from the document's own structure."""
    if export_format == "markdown":
        pattern = r"^- \[(.+)\]\(<([^>\n]+)>\)$"
        return [(match[1], match[2]) for match in re.finditer(pattern, content.decode("utf-8"), flags=re.MULTILINE)]
    with zipfile.ZipFile(BytesIO(content)) as package:
        relationships = ET.fromstring(package.read("word/_rels/document.xml.rels"))
        document = ET.fromstring(package.read("word/document.xml"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships
        if relationship.get("Type") == HYPERLINK_RELATIONSHIP and relationship.get("TargetMode") == "External"
    }
    return [
        ("".join(text.text or "" for text in link.iter(f"{WORD_NAMESPACE}t")), targets[link.attrib[RELATIONSHIP_ID]])
        for link in document.iter(f"{WORD_NAMESPACE}hyperlink")
    ]


def _bold_paragraph(content: bytes, text: str) -> bool:
    """Whether the Word paragraph with exactly ``text``, in the body or a table cell, is bold throughout."""
    document = Document(BytesIO(content))
    cells = [cell for table in document.tables for row in table.rows for cell in row.cells]
    paragraphs = [*document.paragraphs, *(paragraph for cell in cells for paragraph in cell.paragraphs)]
    paragraph = next(paragraph for paragraph in paragraphs if paragraph.text == text)
    return all(run.bold for run in paragraph.runs)


def _assert_attachment(*, status_code: int, headers: Mapping[str, str], filename_slug: str, export_format: str) -> None:
    assert status_code == 200
    assert headers["content-type"] == MEDIA_TYPES[export_format]
    assert re.fullmatch(
        rf'attachment; filename="copyrit-findings-{filename_slug}-{UTC_STAMP}\.{EXTENSIONS[export_format]}"',
        headers["content-disposition"],
    )


async def _add_finding_async(memory: SQLiteMemory, *, operation: Operation, title: str, **fields: object) -> Finding:
    fields.setdefault("severity", "moderate")
    return await memory.add_finding_async(finding=Finding(operation_id=operation.id, title=title, **fields))


async def _add_attack_async(memory: SQLiteMemory, *, conversation_id: str, **fields: object) -> AttackResult:
    attack = AttackResult(conversation_id=conversation_id, **fields)
    await memory.add_attack_results_to_memory_async(attack_results=[attack])
    await memory.add_message_to_memory_async(
        request=Message(
            message_pieces=[
                MessagePiece(role="user", original_value=TRANSCRIPT_MARKER, conversation_id=conversation_id)
            ]
        )
    )
    return attack


async def _link_async(memory: SQLiteMemory, *, finding: Finding, attack_result_id: str | UUID) -> FindingEvidence:
    return await memory.add_finding_evidence_async(
        evidence=FindingEvidence(finding_id=finding.id, attack_result_id=UUID(str(attack_result_id)))
    )


async def _saved_state_async(memory: SQLiteMemory, *, operation: Operation) -> tuple[object, ...]:
    findings = await memory.get_findings_async(operation_id=operation.id, limit=1000)
    evidence = [await memory.get_finding_evidence_async(finding_id=f.id, limit=1000, offset=0) for f in findings]
    attack_ids = sorted({str(item.attack_result_id) for page in evidence for item in page})
    attacks = await memory.get_attack_results_async(attack_result_ids=attack_ids) if attack_ids else []
    return findings, evidence, sorted(attacks, key=lambda attack: attack.attack_result_id)


async def _save_named_findings_async(
    memory: SQLiteMemory, *, filler_count: int
) -> tuple[Operation, dict[str, Finding]]:
    operation = await memory.add_operation_async(Operation(name="Synthetic evaluation"))
    named = {
        "Critical finding": await _add_finding_async(
            memory,
            operation=operation,
            title="Critical finding",
            severity="critical",
            harm_type=HarmCategory.PROFANITY,
            description="Synthetic description\nsecond line",
        ),
        "Included finding": await _add_finding_async(memory, operation=operation, title="Included finding"),
        "Excluded finding": await _add_finding_async(memory, operation=operation, title="Excluded finding"),
    }
    for index in range(filler_count):
        await _add_finding_async(memory, operation=operation, title=f"Filler {index:02d}")
    named["Low finding"] = await _add_finding_async(memory, operation=operation, title="Low finding", severity="low")
    attack = await _add_attack_async(
        memory, conversation_id="main", objective="Restricted-marker test", operation_id=operation.id
    )
    await _link_async(memory, finding=named["Critical finding"], attack_result_id=attack.attack_result_id)
    await _link_async(memory, finding=named["Excluded finding"], attack_result_id=attack.attack_result_id)
    return operation, named


async def _critical_evidence_async(memory: SQLiteMemory, *, named: dict[str, Finding]) -> FindingEvidence:
    (evidence,) = await memory.get_finding_evidence_async(finding_id=named["Critical finding"].id, limit=10, offset=0)
    return evidence


def _single_finding_report(
    *, fields: tuple[_ReportField, ...], evidence: tuple[_ReportEvidence, ...]
) -> _FindingReport:
    finding = _ReportFinding(
        title="Synthetic finding",
        severity=_ReportField(label="Severity", value="Low"),
        fields=fields,
        evidence=evidence,
    )
    return _FindingReport(operation_name="Synthetic evaluation", findings=(finding,))


def _split_fenced_blocks(markdown: str) -> tuple[list[str], list[str]]:
    """
    Separate lines outside fenced blocks from each block's literal body, following CommonMark's fence rules.

    A fence nested in a list item is indented with the item's content, so that indentation is removed from its body.
    """
    outside: list[str] = []
    bodies: list[str] = []
    fence: str | None = None
    indent = 0
    body: list[str] = []
    for line in markdown.split("\n"):
        if fence is None:
            opening = re.match(r"( *)(`{3,}|~{3,})", line)
            if opening:
                indent, fence, body = len(opening.group(1)), opening.group(2), []
            else:
                outside.append(line)
        elif re.fullmatch(rf" *{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line):
            bodies.append("\n".join(body))
            fence = None
        else:
            body.append(line[indent:] if line.startswith(" " * indent) else line.lstrip(" "))
    assert fence is None, "A fenced block was left open"
    return outside, bodies


@pytest.fixture
async def named_findings_async(sqlite_instance: SQLiteMemory) -> tuple[Operation, dict[str, Finding]]:
    return await _save_named_findings_async(sqlite_instance, filler_count=0)


@pytest.fixture
async def paged_findings_async(sqlite_instance: SQLiteMemory) -> tuple[Operation, dict[str, Finding]]:
    """Save 101 findings, so the lowest severity one is past the first 100-row page."""
    return await _save_named_findings_async(sqlite_instance, filler_count=97)


@pytest.mark.parametrize("export_format", FORMATS)
async def test_export_downloads_selected_findings_in_dashboard_order(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    named_findings_async: tuple[Operation, dict[str, Finding]],
    export_format: str,
) -> None:
    operation, named = named_findings_async
    evidence = await _critical_evidence_async(sqlite_instance, named=named)
    before = await _saved_state_async(sqlite_instance, operation=operation)
    selected = [named[title].id for title in ["Low finding", "Included finding", "Critical finding"]]

    response = _export(
        headers=compatibility_headers, operation_id=operation.id, finding_ids=selected, export_format=export_format
    )

    _assert_attachment(
        status_code=response.status_code,
        headers=response.headers,
        filename_slug="synthetic-evaluation",
        export_format=export_format,
    )
    assert _finding_titles(response.content, export_format) == ["Critical finding", "Included finding", "Low finding"]
    assert _links(response.content, export_format) == [
        ("Restricted-marker test", _dashboard_link(evidence=evidence, conversation_id="main"))
    ]
    assert await _saved_state_async(sqlite_instance, operation=operation) == before


async def test_export_defaults_to_docx(
    compatibility_headers: dict[str, str], named_findings_async: tuple[Operation, dict[str, Finding]]
) -> None:
    operation, named = named_findings_async

    response = _export(
        headers=compatibility_headers,
        operation_id=operation.id,
        finding_ids=[named["Included finding"].id],
        export_format=None,
    )

    _assert_attachment(
        status_code=response.status_code,
        headers=response.headers,
        filename_slug="synthetic-evaluation",
        export_format="docx",
    )
    assert _paragraphs(response.content)[:5] == [
        "Findings - Synthetic evaluation",
        EVIDENCE_NOTE,
        "Finding",
        "Assessment",
        "Included finding",
    ]


@pytest.mark.parametrize(
    ("operation_name", "expected_name"),
    [
        ("Synthetic evaluation", "synthetic-evaluation"),
        ("Q3 Red Team / Copilot (v2)", "q3-red-team-copilot-v2"),
        ("Évaluation: modèle 2", "évaluation-modèle-2"),
        ("E\u0301valuation", "évaluation"),
        # Devanagari writes vowel signs and viramas as combining marks, which belong to their word.
        ("हिन्दी परीक्षण", "हिन्दी-परीक्षण"),
        ("snake_case name", "snake-case-name"),
        ("a" * 58 + " bcdef", "a" * 58),
        ("x" * 80, "x" * 60),
        # The 61st code point is a vowel sign, so the cut moves back before its letter.
        ("क" + "कि" * 30, "क" + "कि" * 29),
        ("???", str(FILENAME_OPERATION_ID)),
    ],
)
def test_export_filename_names_the_operation_in_labelled_utc(operation_name: str, expected_name: str) -> None:
    filename = finding_export._export_filename(
        operation_id=FILENAME_OPERATION_ID, operation_name=operation_name, extension="docx", now=FILENAME_TIME
    )

    assert filename == f"copyrit-findings-{expected_name}-2026-10-22-1658-utc.docx"


async def test_export_encodes_a_non_ascii_filename_in_the_header(
    sqlite_instance: SQLiteMemory, compatibility_headers: dict[str, str]
) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Évaluation 中文"))
    finding = await _add_finding_async(sqlite_instance, operation=operation, title="Unicode finding")

    response = _export(
        headers=compatibility_headers, operation_id=operation.id, finding_ids=[finding.id], export_format="markdown"
    )

    assert response.status_code == 200
    assert re.fullmatch(
        rf"attachment; filename\*=utf-8''copyrit-findings-%C3%A9valuation-%E4%B8%AD%E6%96%87-{UTC_STAMP}\.md",
        response.headers["content-disposition"],
    )


@pytest.mark.parametrize(
    ("case", "status_code"),
    [
        ("empty", 422),
        ("duplicate", 422),
        ("malformed", 422),
        ("missing_field", 422),
        ("unknown_field", 422),
        ("unknown_format", 422),
        ("foreign", 404),
        ("missing_finding", 404),
        ("missing_operation", 404),
    ],
)
async def test_export_rejects_invalid_selection(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    named_findings_async: tuple[Operation, dict[str, Finding]],
    case: str,
    status_code: int,
) -> None:
    operation, named = named_findings_async
    other = await sqlite_instance.add_operation_async(Operation(name="Other operation"))
    foreign = await _add_finding_async(sqlite_instance, operation=other, title="Foreign finding")
    before = await _saved_state_async(sqlite_instance, operation=operation)
    finding_id = str(named["Included finding"].id)
    body: dict[str, object] = {"finding_ids": [finding_id], "dashboard_base_url": DASHBOARD_BASE}
    path_operation_id: UUID = operation.id
    if case == "empty":
        body["finding_ids"] = []
    elif case == "duplicate":
        body["finding_ids"] = [finding_id, finding_id.upper()]
    elif case == "malformed":
        body["finding_ids"] = [finding_id, "not-a-uuid"]
    elif case == "missing_field":
        del body["finding_ids"]
    elif case == "unknown_field":
        body["all"] = True
    elif case == "unknown_format":
        body["format"] = "pdf"
    elif case == "foreign":
        body["finding_ids"] = [finding_id, str(foreign.id)]
    elif case == "missing_finding":
        body["finding_ids"] = [finding_id, str(MISSING_ID)]
    else:
        path_operation_id = MISSING_ID

    response = _client().post(
        f"/api/operations/{path_operation_id}/findings/export", json=body, headers=compatibility_headers
    )

    assert response.status_code == status_code
    assert "content-disposition" not in response.headers
    assert await _saved_state_async(sqlite_instance, operation=operation) == before


@pytest.mark.parametrize(
    "dashboard_base_url",
    [
        None,
        "",
        "/team/",
        "pyrit.example.test",
        "ftp://pyrit.example.test/",
        "javascript:alert(1)",
        "******pyrit.example.test/",
        "https://pyrit.example.test/?next=/",
        "https://pyrit.example.test/?",
        "https://pyrit.example.test/#top",
        "https://pyrit.example.test/#",
    ],
)
def test_export_rejects_invalid_dashboard_base(
    compatibility_headers: dict[str, str], dashboard_base_url: str | None
) -> None:
    body: dict[str, object] = {"finding_ids": [str(uuid4())]}
    if dashboard_base_url is not None:
        body["dashboard_base_url"] = dashboard_base_url

    response = _client().post(f"/api/operations/{uuid4()}/findings/export", json=body, headers=compatibility_headers)

    assert response.status_code == 422
    assert "content-disposition" not in response.headers


@pytest.mark.parametrize(
    ("export_format", "failing"),
    [
        ("docx", "get_findings_async"),
        ("markdown", "get_attack_results_async"),
        ("docx", "_render_docx"),
        ("markdown", "_render_markdown"),
    ],
)
async def test_export_fails_instead_of_returning_partial_content(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    named_findings_async: tuple[Operation, dict[str, Finding]],
    export_format: str,
    failing: str,
) -> None:
    operation, named = named_findings_async
    owner = finding_export if failing.startswith("_render") else sqlite_instance

    with patch.object(owner, failing, side_effect=RuntimeError("synthetic failure")):
        response = _export(
            headers=compatibility_headers,
            operation_id=operation.id,
            finding_ids=[named["Critical finding"].id],
            export_format=export_format,
        )

    assert response.status_code == 500
    assert "content-disposition" not in response.headers
    assert response.json()["type"] == "/errors/internal-error"


async def test_report_selects_findings_across_pages_in_dashboard_order(
    sqlite_instance: SQLiteMemory, paged_findings_async: tuple[Operation, dict[str, Finding]]
) -> None:
    operation, named = paged_findings_async
    evidence = await _critical_evidence_async(sqlite_instance, named=named)
    # The request order does not override the dashboard's severity-first order.
    selected = [named[title].id for title in ["Low finding", "Included finding", "Critical finding"]]

    report = await _report_async(operation_id=operation.id, finding_ids=selected)

    # The attack belongs to the report's own Operation, so its evidence does not name it again.
    assert report == _FindingReport(
        operation_name="Synthetic evaluation",
        findings=(
            _ReportFinding(
                title="Critical finding",
                severity=_ReportField(label="Severity", value="Critical"),
                fields=(
                    _ReportField(label="Harm-type", value="Profanity", column="assessment"),
                    _ReportField(label="Description", value="Synthetic description\nsecond line"),
                ),
                evidence=(
                    _ReportEvidence(
                        title="Restricted-marker test",
                        summary=(_ReportField(label="Outcome", value="Undetermined"), NOT_SCORED),
                        details=(),
                        link_url=_dashboard_link(evidence=evidence, conversation_id="main"),
                    ),
                ),
            ),
            _ReportFinding(
                title="Included finding",
                severity=_ReportField(label="Severity", value="Moderate"),
                fields=(),
                evidence=(),
            ),
            _ReportFinding(
                title="Low finding", severity=_ReportField(label="Severity", value="Low"), fields=(), evidence=()
            ),
        ),
    )


async def test_report_follows_dashboard_listing_order(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Listing order"))
    findings = [
        await _add_finding_async(
            sqlite_instance, operation=operation, title=f"{severity.capitalize()} finding", severity=severity
        )
        for severity in ["critical", "moderate", "low"]
    ]
    list_findings_async = OperationService.list_findings_async

    async def _reversed_listing_async(
        service: OperationService, *, operation_id: UUID, limit: int, offset: int
    ) -> FindingListResponse:
        page = await list_findings_async(service, operation_id=operation_id, limit=limit, offset=offset)
        return page.model_copy(update={"items": page.items[::-1]})

    with patch.object(OperationService, "list_findings_async", autospec=True, side_effect=_reversed_listing_async):
        report = await _report_async(operation_id=operation.id, finding_ids=[finding.id for finding in findings])

    assert [finding.title for finding in report.findings] == ["Low finding", "Moderate finding", "Critical finding"]


async def test_report_includes_all_evidence_pages(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence pages"))
    finding = await _add_finding_async(sqlite_instance, operation=operation, title="Many references")
    attacks = [
        AttackResult(conversation_id=f"conversation-{index:03d}", objective=f"Objective {index:03d}")
        for index in range(101)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=attacks)
    await sqlite_instance.add_message_pieces_to_memory_async(
        message_pieces=[
            MessagePiece(role="user", original_value=TRANSCRIPT_MARKER, conversation_id=attack.conversation_id)
            for attack in attacks
        ]
    )
    evidence = [
        await _link_async(sqlite_instance, finding=finding, attack_result_id=a.attack_result_id) for a in attacks
    ]

    report = await _report_async(operation_id=operation.id, finding_ids=[finding.id])

    (exported,) = report.findings
    assert len(exported.evidence) == 101
    assert set(exported.evidence) == {
        _ReportEvidence(
            title=f"Objective {index:03d}",
            summary=(_ReportField(label="Outcome", value="Undetermined"), NOT_SCORED),
            details=(NOT_IN_AN_OPERATION,),
            link_url=_dashboard_link(evidence=item, conversation_id=f"conversation-{index:03d}"),
        )
        for index, item in enumerate(evidence)
    }


async def test_report_uses_effective_score_and_conversation(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Scores"))
    source = await sqlite_instance.add_operation_async(Operation(name="Source operation"))
    finding = await _add_finding_async(sqlite_instance, operation=operation, title="Scored finding")
    pieces = {
        conversation_id: MessagePiece(
            role="assistant", original_value=TRANSCRIPT_MARKER, conversation_id=conversation_id
        )
        for conversation_id in ["main", ODD_CONVERSATION_ID]
    }
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=list(pieces.values()))

    def make_score(conversation_id: str, scorer: str, value: str) -> Score:
        return Score(
            score_value=value,
            score_type="true_false",
            score_rationale=f"{scorer} rationale",
            scorer_class_identifier=ComponentIdentifier(class_name=scorer, class_module="pyrit.score"),
            message_piece_id=pieces[conversation_id].id,
        )

    automated = make_score("main", "SelfAskTrueFalseScorer", "true")
    human = make_score(ODD_CONVERSATION_ID, "ManualScorer", "false")
    await sqlite_instance.add_scores_to_memory_async(scores=[automated, human])
    attack = AttackResult(
        conversation_id="main",
        objective="Scored objective",
        operation_id=source.id,
        automated_score=automated,
        human_score=human,
        outcome=AttackOutcome.FAILURE,
        related_conversations={
            ConversationReference(conversation_id=ODD_CONVERSATION_ID, conversation_type=ConversationType.PRUNED)
        },
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    evidence = await _link_async(sqlite_instance, finding=finding, attack_result_id=attack.attack_result_id)

    report = await _report_async(operation_id=operation.id, finding_ids=[finding.id])

    # The human score is the attack's current score, so the branch it was given in is the one to open.
    assert report.findings == (
        _ReportFinding(
            title="Scored finding",
            severity=_ReportField(label="Severity", value="Moderate"),
            fields=(),
            evidence=(
                _ReportEvidence(
                    title="Scored objective",
                    summary=(
                        _ReportField(label="Outcome", value="Failure"),
                        _ReportField(label="Score", value="False (human review)"),
                    ),
                    details=(
                        _ReportField(label="Rationale", value="ManualScorer rationale"),
                        _ReportField(label="From operation", value="Source operation"),
                    ),
                    link_url=_dashboard_link(evidence=evidence, conversation_id=ENCODED_CONVERSATION_ID),
                ),
            ),
        ),
    )


async def test_report_retains_undetermined_score(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Undetermined"))
    finding = await _add_finding_async(sqlite_instance, operation=operation, title="Undecided finding")
    attack = await _add_attack_async(sqlite_instance, conversation_id="main", objective="Undecided objective")
    piece = (await sqlite_instance.get_message_pieces_async(conversation_id="main"))[0]
    score = Score(
        score_value=None,
        status=ScoreStatus.UNDETERMINED,
        score_type="true_false",
        score_rationale="The scorer could not decide.",
        scorer_class_identifier=ComponentIdentifier(class_name="SelfAskTrueFalseScorer", class_module="pyrit.score"),
        message_piece_id=piece.id,
    )
    await sqlite_instance.add_scores_to_memory_async(scores=[score])
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id, update_fields={"automated_score_id": str(score.id)}
    )
    evidence = await _link_async(sqlite_instance, finding=finding, attack_result_id=attack.attack_result_id)

    report = await _report_async(operation_id=operation.id, finding_ids=[finding.id])

    (exported,) = report.findings
    assert exported.evidence == (
        _ReportEvidence(
            title="Undecided objective",
            summary=(
                _ReportField(label="Outcome", value="Undetermined"),
                _ReportField(label="Score", value="Undetermined (SelfAskTrueFalseScorer)"),
            ),
            details=(_ReportField(label="Rationale", value="The scorer could not decide."), NOT_IN_AN_OPERATION),
            link_url=_dashboard_link(evidence=evidence, conversation_id="main"),
        ),
    )


async def test_report_retains_unavailable_evidence(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Unavailable"))
    finding = await _add_finding_async(sqlite_instance, operation=operation, title="Unavailable finding")
    missing_attack_id = uuid4()
    await _link_async(sqlite_instance, finding=finding, attack_result_id=missing_attack_id)
    messageless = AttackResult(conversation_id="nothing-saved", objective="Messageless objective")
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[messageless])
    await _link_async(sqlite_instance, finding=finding, attack_result_id=messageless.attack_result_id)

    report = await _report_async(operation_id=operation.id, finding_ids=[finding.id])

    (exported,) = report.findings
    assert len(exported.evidence) == 2
    # Without an objective, the saved attack ID leads the entry, as on the dashboard.
    assert set(exported.evidence) == {
        _ReportEvidence(title=str(missing_attack_id), summary=(), details=(), link_url=None),
        _ReportEvidence(
            title="Messageless objective",
            summary=(_ReportField(label="Outcome", value="Undetermined"), NOT_SCORED),
            details=(NOT_IN_AN_OPERATION,),
            link_url=None,
        ),
    }


async def test_report_projects_optional_and_custom_fields(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Classifications"))
    custom = await _add_finding_async(
        sqlite_instance,
        operation=operation,
        title="Custom finding",
        severity="other",
        severity_other="Team severity",
        harm_type=HarmCategory.OTHER,
        harm_type_other="Team harm",
    )
    plain = await _add_finding_async(
        sqlite_instance, operation=operation, title="Plain finding", severity="informational", description="Only text"
    )
    attack = await _add_attack_async(sqlite_instance, conversation_id="main", objective="  ")
    evidence = await _link_async(sqlite_instance, finding=custom, attack_result_id=attack.attack_result_id)

    report = await _report_async(operation_id=operation.id, finding_ids=[custom.id, plain.id])

    # Other sorts after every named severity, as on the dashboard.
    assert report.findings == (
        _ReportFinding(
            title="Plain finding",
            severity=_ReportField(label="Severity", value="Informational"),
            fields=(_ReportField(label="Description", value="Only text"),),
            evidence=(),
        ),
        _ReportFinding(
            title="Custom finding",
            severity=_ReportField(label="Severity", value="Team severity"),
            fields=(_ReportField(label="Harm-type", value="Team harm", column="assessment"),),
            evidence=(
                _ReportEvidence(
                    title="No objective",
                    summary=(_ReportField(label="Outcome", value="Undetermined"), NOT_SCORED),
                    details=(NOT_IN_AN_OPERATION,),
                    link_url=_dashboard_link(evidence=evidence, conversation_id="main"),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("score_value", "scorer", "expected"),
    [
        ("true", "SubStringScorer", "True (SubStringScorer)"),
        ("false", "ManualScorer", "False (human review)"),
        ("0.75", "SelfAskLikertScorer", "0.75 (SelfAskLikertScorer)"),
    ],
)
def test_score_text_capitalizes_booleans_and_names_human_review(score_value: str, scorer: str, expected: str) -> None:
    score = Score(
        score_value=score_value,
        score_type="float_scale" if score_value == "0.75" else "true_false",
        score_rationale="Synthetic rationale",
        scorer_class_identifier=ComponentIdentifier(class_name=scorer, class_module="pyrit.score"),
        message_piece_id=uuid4(),
    )

    assert finding_export._score_text(ScoreView.from_domain(score)) == expected


LINK = "https://pyrit.example.test/attacks/a/conversations/b?findingEvidenceId=c"
TABLE_REPORT = _FindingReport(
    operation_name="Synthetic evaluation",
    findings=(
        _ReportFinding(
            title="First finding",
            severity=_ReportField(label="Severity", value="Critical"),
            fields=(
                _ReportField(label="Harm-type", value="Synthetic harm", column="assessment"),
                _ReportField(label="Description", value="Synthetic description"),
            ),
            evidence=(
                _ReportEvidence(
                    title="Linked objective",
                    summary=(
                        _ReportField(label="Outcome", value="Success"),
                        _ReportField(label="Score", value="True (SubStringScorer)"),
                    ),
                    details=(_ReportField(label="Rationale", value="Synthetic rationale"),),
                    link_url=LINK,
                ),
                _ReportEvidence(
                    title="Unscored objective",
                    summary=(_ReportField(label="Outcome", value="Failure"), NOT_SCORED),
                    details=(_ReportField(label="From operation", value="Synthetic source"),),
                    link_url=LINK,
                ),
                _ReportEvidence(title="attack-404", summary=(), details=(), link_url=None),
            ),
        ),
        _ReportFinding(
            title="Second finding", severity=_ReportField(label="Severity", value="Low"), fields=(), evidence=()
        ),
    ),
)


def test_docx_opens_as_a_current_word_document() -> None:
    content = _render_docx(_single_finding_report(fields=(), evidence=()), now=FILENAME_TIME)

    document = Document(BytesIO(content))
    with zipfile.ZipFile(BytesIO(content)) as package:
        names = package.namelist()
        settings = package.read("word/settings.xml").decode("utf-8")
        styles = package.read("word/styles.xml").decode("utf-8")
    # Word opens mode 14 files in Compatibility Mode.
    assert (
        re.search(r'w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"', settings)
        is not None
    )
    properties = document.core_properties
    assert properties.title == "Findings - Synthetic evaluation"
    assert (properties.author, properties.comments, properties.last_modified_by) == ("", "", "")
    # The moment the server's filename names, stored in UTC.
    assert properties.created is not None and f"{properties.created:%Y-%m-%d %H:%M:%S}" == "2026-10-22 16:58:33"
    assert properties.modified == properties.created
    assert not [name for name in names if name in {"docProps/app.xml", "docProps/thumbnail.jpeg"}]
    # Latin fonts and text colors are explicit rather than taken from the template's 2007-era theme.
    assert re.search(r"w:(ascii|hAnsi)Theme=", styles) is None
    assert re.search(r'w:ascii="Calibri"', styles) is not None
    title_style = document.styles["Heading 1"].font
    assert (str(title_style.color.rgb), title_style.size.pt if title_style.size else None, title_style.bold) == (
        "2F5496",
        16,
        False,
    )
    assert re.search(r"<w:color [^>]*w:themeColor=", styles) is None


def test_docx_lists_every_finding_in_one_finding_and_assessment_table() -> None:
    content = _render_docx(TABLE_REPORT, now=FILENAME_TIME)

    document = Document(BytesIO(content))
    assert [paragraph.text for paragraph in document.paragraphs] == ["Findings - Synthetic evaluation", EVIDENCE_NOTE]
    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.style is not None and table.style.name == "Table Grid"
    header = table.rows[0]
    assert [cell.text for cell in header.cells] == ["Finding", "Assessment"]
    assert all(run.bold for cell in header.cells for run in cell.paragraphs[0].runs)
    # The header appears once, at the top of the table, rather than repeating on later pages.
    assert header._tr.trPr is None or header._tr.trPr.find(qn("w:tblHeader")) is None
    assert _finding_cells(content) == [
        (
            [
                "First finding",
                "Description: Synthetic description",
                "Evidence:",
                "Linked objective",
                "Outcome: Success · Score: True (SubStringScorer)",
                "Rationale: Synthetic rationale",
                "Unscored objective",
                "Outcome: Failure · Score: Not scored",
                # Each entry keeps the same shape: the summary line, then one line per detail.
                "From operation: Synthetic source",
                "attack-404",
                "Evidence unavailable",
            ],
            ["Severity: Critical", "Harm-type: Synthetic harm"],
        ),
        (["Second finding", "Evidence: none attached"], ["Severity: Low"]),
    ]
    assert _bold_paragraph(content, "First finding")
    assert _bold_paragraph(content, "attack-404")
    assert _links(content, "docx") == [("Linked objective", LINK), ("Unscored objective", LINK)]
    # Each entry starts with a bullet, and its lines sit indented under the bullet's text.
    evidence_paragraphs = table.rows[1].cells[0].paragraphs[3:]
    styles = [paragraph.style.name if paragraph.style else None for paragraph in evidence_paragraphs]
    assert styles == ["List Bullet", "Normal", "Normal", "List Bullet", "Normal", "Normal", "List Bullet", "Normal"]
    indents = [paragraph.paragraph_format.left_indent for paragraph in evidence_paragraphs]
    assert [indent.pt if indent is not None else None for indent in indents] == [
        None,
        18,
        18,
        None,
        18,
        18,
        None,
        18,
    ]


def test_markdown_reads_like_the_word_table_top_to_bottom() -> None:
    markdown = _render_markdown(TABLE_REPORT)

    # Assessment fields lead under the title, as the Assessment column sits beside it in Word.
    assert markdown.split("\n") == [
        "# Findings - Synthetic evaluation",
        "",
        EVIDENCE_NOTE,
        "",
        "## First finding",
        "",
        "**Severity:** Critical",
        "",
        "**Harm-type:** Synthetic harm",
        "",
        "**Description:** Synthetic description",
        "",
        "**Evidence:**",
        "",
        f"- [Linked objective](<{LINK}>)",
        "  - **Outcome:** Success · **Score:** True (SubStringScorer)",
        "  - **Rationale:** Synthetic rationale",
        f"- [Unscored objective](<{LINK}>)",
        "  - **Outcome:** Failure · **Score:** Not scored",
        "  - **From operation:** Synthetic source",
        "- **attack-404**",
        "  - Evidence unavailable",
        "",
        "## Second finding",
        "",
        "**Severity:** Low",
        "",
        "**Evidence:** none attached",
        "",
    ]


def test_renderers_keep_arbitrary_projected_fields() -> None:
    evidence = _ReportEvidence(
        title="Source text",
        summary=(_ReportField(label="Synthetic evidence field", value="evidence value"),),
        details=(_ReportField(label="Synthetic evidence notes", value="note one\nnote two"),),
        link_url=None,
    )
    report = _FindingReport(
        operation_name="Synthetic evaluation",
        findings=(
            _ReportFinding(
                title="Configured finding",
                severity=_ReportField(label="Synthetic severity", value="Level 3"),
                fields=(
                    _ReportField(label="Synthetic custom field", value="line one\nline two"),
                    _ReportField(label="Synthetic narrative", value="part one\npart two"),
                    _ReportField(label="Synthetic class", value="Class B", column="assessment"),
                ),
                evidence=(evidence,),
            ),
        ),
    )

    content = _render_docx(report, now=FILENAME_TIME)
    markdown = _render_markdown(report)

    # The projection, not the renderer, decides each field's column; severity always leads the Assessment.
    assert _finding_cells(content) == [
        (
            [
                "Configured finding",
                "Synthetic custom field: line one\nline two",
                "Synthetic narrative: part one\npart two",
                "Evidence:",
                "Source text",
                "Synthetic evidence field: evidence value",
                "Synthetic evidence notes: note one\nnote two",
                "Evidence unavailable",
            ],
            ["Synthetic severity: Level 3", "Synthetic class: Class B"],
        )
    ]
    assert _bold_paragraph(content, "Source text")
    # A value that spans lines is fenced rather than flattened, inside an evidence bullet too.
    assert markdown.split("\n") == [
        "# Findings - Synthetic evaluation",
        "",
        EVIDENCE_NOTE,
        "",
        "## Configured finding",
        "",
        "**Synthetic severity:** Level 3",
        "",
        "**Synthetic class:** Class B",
        "",
        "**Synthetic custom field**",
        "",
        "```text",
        "line one",
        "line two",
        "```",
        "",
        "**Synthetic narrative**",
        "",
        "```text",
        "part one",
        "part two",
        "```",
        "",
        "**Evidence:**",
        "",
        "- **Source text**",
        "  - **Synthetic evidence field:** evidence value",
        "  - **Synthetic evidence notes**",
        "",
        "    ```text",
        "    note one",
        "    note two",
        "    ```",
        "  - Evidence unavailable",
        "",
    ]


def test_markdown_fences_literal_body_and_escapes_inline_values() -> None:
    link = "https://pyrit.example.test/team(a)/attacks/a/conversations/b?findingEvidenceId=c"
    evidence = _ReportEvidence(
        title=HOSTILE_BODY,
        summary=(
            _ReportField(label="Outcome", value="Success\n<script>alert(1)</script>"),
            _ReportField(label="Score", value="True <img src=x> [l](x)"),
        ),
        details=(_ReportField(label="Rationale", value="`````\n<img src=x>"),),
        link_url=link,
    )
    report = _FindingReport(
        operation_name="Ops <b>bold</b>\n# Forged operation",
        findings=(
            _ReportFinding(
                title="First `code`\n## 9. Forged finding <img src=x> ![i](https://evil.example/i.png) [l](x)",
                severity=_ReportField(label="Severity", value="Custom &amp; ~~struck~~ | $x$ # Forged severity"),
                fields=(_ReportField(label="Description", value=HOSTILE_BODY),),
                evidence=(evidence,),
            ),
            _ReportFinding(
                title="Second Ünïcødé 😀 中文 finding",
                severity=_ReportField(label="Severity", value="Low"),
                fields=(),
                evidence=(),
            ),
        ),
    )

    markdown = _render_markdown(report)

    outside, bodies = _split_fenced_blocks(markdown)
    assert markdown.endswith("\n")
    # Values that span lines are fenced, even inside an evidence bullet.
    assert bodies == [HOSTILE_BODY, "Success\n<script>alert(1)</script>", "`````\n<img src=x>"]
    headings = [line for line in outside if line.startswith("#")]
    assert headings == [
        r"# Findings - Ops \<b\>bold\</b\> \# Forged operation",
        r"## First \`code\` \#\# 9. Forged finding \<img src=x\> !\[i\](https://evil.example/i.png) \[l\](x)",
        "## Second Ünïcødé 😀 中文 finding",
    ]
    assert r"**Severity:** Custom \&amp; \~\~struck\~\~ \| \$x\$ \# Forged severity" in outside
    assert r"  - **Score:** True \<img src=x\> \[l\](x)" in outside
    assert "**Evidence:** none attached" in outside
    # The objective is the link's text, escaped onto one line so it cannot close the link or open another.
    link_lines = [line for line in outside if line.startswith("- [") and line.endswith(f"](<{link}>)")]
    assert len(link_lines) == 1
    link_text = link_lines[0][len("- [") : -len(f"](<{link}>)")]
    assert "\n" not in link_text and re.search(r"(?<!\\)[<>\[\]`]", link_text) is None
    # Authored text never opens HTML, a link, an image, or code outside a literal block.
    assert [line for line in outside if line not in link_lines and re.search(r"(?<!\\)[<>\[\]`]", line)] == []


@pytest.mark.parametrize(
    ("dashboard_base_url", "expected_base"),
    [
        (DASHBOARD_BASE, "https://pyrit.example.test/team(a)"),
        ("https://pyrit.example.test/team(a)", "https://pyrit.example.test/team(a)"),
        ("http://localhost:3073", "http://localhost:3073"),
    ],
)
def test_renderers_preserve_prefixed_evidence_links(dashboard_base_url: str, expected_base: str) -> None:
    request = FindingExportRequest.model_validate({"finding_ids": [uuid4()], "dashboard_base_url": dashboard_base_url})
    scenario_result_id = uuid4()
    item = FindingEvidenceItem(
        item=FindingEvidence(finding_id=uuid4(), attack_result_id=uuid4()),
        availability=EvidenceAvailability.AVAILABLE,
        scenario_result_id=scenario_result_id,
        objective="Linked objective",
        outcome=AttackOutcome.SUCCESS,
        conversation_id=ODD_CONVERSATION_ID,
    )
    expected = (
        f"{expected_base}/attacks/{item.item.attack_result_id}/conversations/{ENCODED_CONVERSATION_ID}"
        f"?scenarioResultId={scenario_result_id}&findingEvidenceId={item.item.id}"
    )

    evidence = _project_evidence(item=item, operation_id=uuid4(), dashboard_base_url=request.dashboard_base_url)
    report = _single_finding_report(fields=(), evidence=(evidence,))

    assert evidence.link_url == expected
    assert _links(_render_docx(report, now=FILENAME_TIME), "docx") == [("Linked objective", expected)]
    assert _links(_render_markdown(report).encode("utf-8"), "markdown") == [("Linked objective", expected)]


def test_docx_keeps_literal_text_and_marks_characters_word_cannot_store() -> None:
    report = _single_finding_report(
        fields=(
            _ReportField(label="Description", value=HOSTILE_BODY),
            _ReportField(label="Synthetic control text", value="bell\x07 escape\x1b\r\nnext line"),
        ),
        evidence=(),
    )

    paragraphs = _paragraphs(_render_docx(report, now=FILENAME_TIME))

    assert f"Description: {HOSTILE_BODY}" in paragraphs
    assert "Synthetic control text: bell\ufffd escape\ufffd\nnext line" in paragraphs
    assert "Evidence: none attached" in paragraphs


def test_renderers_break_lines_and_mark_control_characters_alike() -> None:
    report = _single_finding_report(
        fields=(
            _ReportField(label="Breaks", value="crlf\r\nvt\vff\ffs\x1cnel\x85ls\u2028ps\u2029end"),
            _ReportField(label="Controls", value="c1\x80 c1\x9f del\x7f nul\x00 tab\tend"),
        ),
        evidence=(),
    )

    paragraphs = _paragraphs(_render_docx(report, now=FILENAME_TIME))
    markdown = _render_markdown(report)

    assert "Breaks: crlf\nvt\nff\nfs\nnel\nls\nps\nend" in paragraphs
    assert "Controls: c1\ufffd c1\ufffd del\ufffd nul\ufffd tab\tend" in paragraphs
    assert "\n**Breaks**\n\n```text\ncrlf\nvt\nff\nfs\nnel\nls\nps\nend\n```\n" in markdown
    assert "\n**Controls:** c1\ufffd c1\ufffd del\ufffd nul\ufffd tab\tend\n" in markdown
