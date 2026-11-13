# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Word rendering for the paths in a finding report."""

from docx.table import _Cell

from pyrit.output.finding_report.report import (
    PATH_STEP_SEPARATOR,
    PATHS_LABEL,
    STEP_NOT_DEMONSTRATED,
    FindingReportPath,
    plain_text,
)


def add_word_paths(*, cell: _Cell, paths: tuple[FindingReportPath, ...]) -> None:
    """Append paths, their demonstrated steps, and their descriptions to a report cell."""
    cell.add_paragraph().add_run(f"{PATHS_LABEL}:").bold = True
    for path in paths:
        paragraph = cell.add_paragraph(style="List Bullet")
        paragraph.add_run(plain_text(path.name)).bold = True
        paragraph.add_run(f" ({path.status}): ")
        for index, step in enumerate(path.steps):
            if index:
                paragraph.add_run(PATH_STEP_SEPARATOR)
            paragraph.add_run(plain_text(step.label))
            if not step.demonstrated:
                paragraph.add_run(f" ({STEP_NOT_DEMONSTRATED})").italic = True
        if path.description:
            paragraph.add_run(f"\n{plain_text(path.description)}")
