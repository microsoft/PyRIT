# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.output.finding_report.markdown import MarkdownFindingReportPrinter
from pyrit.output.finding_report.report import (
    EVIDENCE_NOTE,
    FindingReport,
    FindingReportColumn,
    FindingReportEntry,
    FindingReportEvidence,
    FindingReportField,
)

LINK = "https://pyrit.example.test/attacks/a/conversations/b?findingEvidenceId=c"
NOT_SCORED = FindingReportField(label="Score", value="Not scored")
HOSTILE_BODY = (
    "Literal ```` run\n````\n# Forged finding\n<img src=x onerror=alert(1)>\n"
    "![image](https://evil.example/i.png)\n[link](https://evil.example)\nÜnïcødé 😀 中文\n"
)
TABLE_REPORT = FindingReport(
    operation_name="Synthetic evaluation",
    findings=(
        FindingReportEntry(
            title="First finding",
            severity=FindingReportField(label="Severity", value="Critical"),
            fields=(
                FindingReportField(label="Harm-type", value="Synthetic harm", column=FindingReportColumn.ASSESSMENT),
                FindingReportField(label="Description", value="Synthetic description"),
            ),
            evidence=(
                FindingReportEvidence(
                    title="Linked objective",
                    summary=(
                        FindingReportField(label="Outcome", value="Success"),
                        FindingReportField(label="Score", value="True (SubStringScorer)"),
                    ),
                    details=(FindingReportField(label="Rationale", value="Synthetic rationale"),),
                    link_url=LINK,
                ),
                FindingReportEvidence(
                    title="Unscored objective",
                    summary=(FindingReportField(label="Outcome", value="Failure"), NOT_SCORED),
                    details=(FindingReportField(label="From operation", value="Synthetic source"),),
                    link_url=LINK,
                ),
                FindingReportEvidence(title="attack-404", summary=(), details=(), link_url=None),
            ),
        ),
        FindingReportEntry(
            title="Second finding",
            severity=FindingReportField(label="Severity", value="Low"),
            fields=(),
            evidence=(),
        ),
    ),
)


def _single_finding_report(
    *, fields: tuple[FindingReportField, ...], evidence: tuple[FindingReportEvidence, ...]
) -> FindingReport:
    finding = FindingReportEntry(
        title="Synthetic finding",
        severity=FindingReportField(label="Severity", value="Low"),
        fields=fields,
        evidence=evidence,
    )
    return FindingReport(operation_name="Synthetic evaluation", findings=(finding,))


def _consecutive_lines(*, markdown: str, expected: list[str]) -> list[str]:
    """The rendered lines from the first expected line on, as many as are expected."""
    lines = markdown.split("\n")
    start = lines.index(expected[0])
    return lines[start : start + len(expected)]


async def test_renders_the_report_top_to_bottom() -> None:
    markdown = await MarkdownFindingReportPrinter().render_async(TABLE_REPORT)

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


async def test_multi_line_values_stay_prose() -> None:
    evidence = FindingReportEvidence(
        title="Source text",
        summary=(FindingReportField(label="Outcome", value="Success"), NOT_SCORED),
        details=(FindingReportField(label="Rationale", value="one\n\ntwo"),),
        link_url=None,
    )
    report = _single_finding_report(
        fields=(
            FindingReportField(label="Description", value="First line\nsecond line"),
            FindingReportField(label="Synthetic class", value="Class B", column=FindingReportColumn.ASSESSMENT),
        ),
        evidence=(evidence,),
    )

    markdown = await MarkdownFindingReportPrinter().render_async(report)

    # Lines end in hard breaks and blank lines separate paragraphs, inside an evidence bullet too.
    assert markdown.split("\n") == [
        "# Findings - Synthetic evaluation",
        "",
        EVIDENCE_NOTE,
        "",
        "## Synthetic finding",
        "",
        "**Severity:** Low",
        "",
        "**Synthetic class:** Class B",
        "",
        "**Description:** First line\\",
        "second line",
        "",
        "**Evidence:**",
        "",
        "- **Source text**",
        "  - **Outcome:** Success · **Score:** Not scored",
        "  - **Rationale:** one",
        "",
        "    two",
        "  - Evidence unavailable",
        "",
    ]


async def test_block_syntax_at_line_starts_is_escaped() -> None:
    description = "- item\n+ item\n1. item\n2) item\n===\n    indented\n# heading\n> quote\n```"
    rationale = FindingReportField(label="Rationale", value="one\n- item\n1. item\n\n===\n    indented")
    evidence = FindingReportEvidence(title="Linked objective", summary=(), details=(rationale,), link_url=LINK)
    report = _single_finding_report(
        fields=(FindingReportField(label="Description", value=description),),
        evidence=(evidence,),
    )

    markdown = await MarkdownFindingReportPrinter().render_async(report)

    # The first line follows the label, so only the lines after it can start a block.
    expected = [
        "**Description:** - item\\",
        "\\+ item\\",
        "1\\. item\\",
        "2\\) item\\",
        "\\===\\",
        "indented\\",
        "\\# heading\\",
        "\\> quote\\",
        "\\`\\`\\`",
        "",
    ]
    assert _consecutive_lines(markdown=markdown, expected=expected) == expected
    # Every paragraph of a multi-paragraph value stays inside its evidence bullet.
    in_bullet = [
        f"- [Linked objective](<{LINK}>)",
        "  - **Rationale:** one\\",
        "    \\- item\\",
        "    1\\. item",
        "",
        "    \\===\\",
        "    indented",
        "",
    ]
    assert _consecutive_lines(markdown=markdown, expected=in_bullet) == in_bullet


async def test_single_line_value_keeps_its_text_after_the_label() -> None:
    report = _single_finding_report(
        fields=(FindingReportField(label="Score", value="0.75 (SyntheticScorer)"),),
        evidence=(),
    )

    markdown = await MarkdownFindingReportPrinter().render_async(report)

    assert "**Score:** 0.75 (SyntheticScorer)" in markdown.split("\n")


async def test_hostile_text_cannot_form_markup() -> None:
    link = "https://pyrit.example.test/attacks/a(1)/conversations/b <c>?findingEvidenceId=d"
    destination = "https://pyrit.example.test/attacks/a(1)/conversations/b%20%3Cc%3E?findingEvidenceId=d"
    evidence = FindingReportEvidence(
        title=HOSTILE_BODY,
        summary=(
            FindingReportField(label="Outcome", value="Success"),
            FindingReportField(label="Score", value="True <img src=x> [l](x)"),
        ),
        details=(FindingReportField(label="Rationale", value="`````\n<img src=x>"),),
        link_url=link,
    )
    report = FindingReport(
        operation_name="Ops <b>bold</b>\n# Forged operation",
        findings=(
            FindingReportEntry(
                title="First `code`\n## 9. Forged finding <img src=x> ![i](https://evil.example/i.png) [l](x)",
                severity=FindingReportField(label="Severity", value="Custom &amp; ~~struck~~ | $x$ # Forged severity"),
                fields=(FindingReportField(label="Description", value=HOSTILE_BODY),),
                evidence=(evidence,),
            ),
            FindingReportEntry(
                title="Second Ünïcødé 😀 中文 finding",
                severity=FindingReportField(label="Severity", value="Low"),
                fields=(),
                evidence=(),
            ),
        ),
    )

    markdown = await MarkdownFindingReportPrinter().render_async(report)

    lines = markdown.split("\n")
    assert markdown.endswith("\n")
    assert [line for line in lines if line.startswith("#")] == [
        r"# Findings - Ops \<b\>bold\</b\> \# Forged operation",
        r"## First \`code\` \#\# 9. Forged finding \<img src=x\> !\[i\](https://evil.example/i.png) \[l\](x)",
        "## Second Ünïcødé 😀 中文 finding",
    ]
    assert r"**Severity:** Custom \&amp; \~\~struck\~\~ \| \$x\$ \# Forged severity" in lines
    assert r"  - **Outcome:** Success · **Score:** True \<img src=x\> \[l\](x)" in lines
    assert "**Evidence:** none attached" in lines
    # The objective is the link's text, escaped onto one line so it cannot close the link or open another.
    link_lines = [line for line in lines if line.startswith("- [") and line.endswith(f"](<{destination}>)")]
    assert len(link_lines) == 1
    link_text = link_lines[0][len("- [") : -len(f"](<{destination}>)")]
    assert re.search(r"(?<!\\)[<>\[\]`]", link_text) is None
    # Authored text never opens HTML, a link, an image, or code.
    assert [line for line in lines if line not in link_lines and re.search(r"(?<!\\)[<>\[\]`]", line)] == []


async def test_commonmark_parses_hostile_paragraphs_as_prose() -> None:
    markdown_it = pytest.importorskip("markdown_it")
    paragraphs = "```\nfenced\n```\n\n    indented code\n\n<div>html</div>\n\n# Forged heading\n\n- Forged item"
    evidence = FindingReportEvidence(
        title=HOSTILE_BODY,
        summary=(FindingReportField(label="Outcome", value="Success"), NOT_SCORED),
        details=(FindingReportField(label="Rationale", value=f"Rationale start\n\n{paragraphs}\n\nRationale end"),),
        link_url=LINK,
    )
    report = FindingReport(
        operation_name="Ops <b>bold</b>\n# Forged operation",
        findings=(
            FindingReportEntry(
                title="First finding\n## Forged finding",
                severity=FindingReportField(label="Severity", value="Critical"),
                fields=(FindingReportField(label="Description", value=f"{HOSTILE_BODY}\n{paragraphs}"),),
                evidence=(evidence,),
            ),
            FindingReportEntry(
                title="Second finding",
                severity=FindingReportField(label="Severity", value="Low"),
                fields=(),
                evidence=(),
            ),
        ),
    )

    markdown = await MarkdownFindingReportPrinter().render_async(report)
    tokens = markdown_it.MarkdownIt("commonmark").parse(markdown)

    children = [child for token in tokens for child in token.children or []]
    assert {token.type for token in tokens + children}.isdisjoint(
        {"fence", "code_block", "html_block", "html_inline", "image"}
    )
    assert [child.attrGet("href") for child in children if child.type == "link_open"] == [LINK]
    # Only the evidence bullet and its summary and rationale items are lists.
    assert sum(token.type == "list_item_open" for token in tokens) == 3
    headings = [
        (token.tag, "".join(child.content for child in tokens[index + 1].children or []))
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    assert headings == [
        ("h1", "Findings - Ops <b>bold</b> # Forged operation"),
        ("h2", "First finding ## Forged finding"),
        ("h2", "Second finding"),
    ]
    # The rationale's last paragraph must sit in the same nested list item as its label.
    open_blocks = []
    list_items = {}
    for token in tokens:
        if token.nesting == 1:
            open_blocks.append(token)
        elif token.nesting == -1:
            open_blocks.pop()
        elif token.type == "inline":
            list_items[token.content] = [block for block in open_blocks if block.type == "list_item_open"]
    assert len(list_items["Rationale end"]) == 2
    assert list_items["Rationale end"][-1] is list_items["**Rationale:** Rationale start"][-1]


async def test_breaks_lines_and_marks_control_characters() -> None:
    report = _single_finding_report(
        fields=(
            FindingReportField(label="Breaks", value="crlf\r\nvt\vff\ffs\x1cnel\x85ls\u2028ps\u2029end"),
            FindingReportField(label="Controls", value="c1\x80 c1\x9f del\x7f nul\x00 tab\tend"),
        ),
        evidence=(),
    )

    markdown = await MarkdownFindingReportPrinter().render_async(report)

    breaks = ["**Breaks:** crlf\\", "vt\\", "ff\\", "fs\\", "nel\\", "ls\\", "ps\\", "end", ""]
    assert _consecutive_lines(markdown=markdown, expected=breaks) == breaks
    assert "**Controls:** c1\ufffd c1\ufffd del\ufffd nul\ufffd tab\tend" in markdown.split("\n")
