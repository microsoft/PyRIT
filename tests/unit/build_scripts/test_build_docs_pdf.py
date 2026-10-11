# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter
from pypdf.errors import PdfReadError

from build_scripts import build_docs_pdf


@pytest.fixture
def doc_root(tmp_path: Path) -> Path:
    (tmp_path / "myst.yml").write_text(
        "project:\n  exports:\n    - format: pdf\n      template: plain_latex_book\n      output: exports/book.pdf\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def command(tmp_path: Path) -> Iterator[MagicMock]:
    run = MagicMock(spec=build_docs_pdf.subprocess.run, return_value=CompletedProcess([], 0))
    with (
        patch.object(build_docs_pdf.shutil, "which", return_value=str(tmp_path / "tool")),
        patch.object(build_docs_pdf.subprocess, "run", new=run),
    ):
        yield run


def _write_output(*, doc_root: Path, log: str = "LaTeX Warning: Label may have changed.\n") -> None:
    output = doc_root / "exports" / "book.pdf"
    output.parent.mkdir(exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    logs = output.parent / "book_pdf_logs"
    logs.mkdir(exist_ok=True)
    (logs / "book.log").write_text(log, encoding="utf-8")
    (logs / "book.shell.log").write_text("Latexmk: All targets (book.pdf) are up-to-date\n", encoding="utf-8")


def test_build_pdf_requires_tools_before_running(*, doc_root: Path, command: MagicMock) -> None:
    with patch.object(build_docs_pdf.shutil, "which", return_value=None):
        with pytest.raises(ValueError, match="Missing PDF prerequisites on PATH: latexmk, xelatex"):
            build_docs_pdf.build_pdf(doc_root)
    command.assert_not_called()


@pytest.mark.parametrize("missing", ["latexmk", "xelatex"])
def test_build_pdf_checks_each_tool(*, doc_root: Path, command: MagicMock, missing: str) -> None:
    with patch.object(build_docs_pdf.shutil, "which", side_effect=lambda tool: None if tool == missing else tool):
        with pytest.raises(ValueError, match=f"Missing PDF prerequisites on PATH: {missing}"):
            build_docs_pdf.build_pdf(doc_root)
    command.assert_not_called()


def test_build_pdf_preserves_native_failure(
    *, doc_root: Path, command: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    command.return_value = CompletedProcess([], 7)
    assert build_docs_pdf.build_pdf(doc_root) == 7
    assert "exited with code 7" in capsys.readouterr().err


def test_build_pdf_rejects_missing_output_after_zero_exit(*, doc_root: Path, command: MagicMock) -> None:
    with pytest.raises(ValueError, match="did not produce a nonempty file"):
        build_docs_pdf.build_pdf(doc_root)
    command.assert_called_once()


def test_build_pdf_rejects_stale_output(*, doc_root: Path, command: MagicMock) -> None:
    _write_output(doc_root=doc_root)
    with pytest.raises(ValueError, match="left a stale file unchanged"):
        build_docs_pdf.build_pdf(doc_root)
    command.assert_called_once()


def test_build_pdf_validates_output_and_preserves_warnings(
    *, doc_root: Path, command: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        _write_output(doc_root=doc_root)
        return CompletedProcess([], 0)

    command.side_effect = render
    assert build_docs_pdf.build_pdf(doc_root) == 0
    assert "Verified fresh PDF export" in capsys.readouterr().out
    command.assert_called_once()
    args, kwargs = command.call_args
    assert args[0] == [
        build_docs_pdf.sys.executable,
        "-m",
        "jupyter_book",
        "build",
        "--site",
        "--pdf",
        "--strict",
        "--logs",
    ]
    assert kwargs == {"cwd": doc_root, "check": False}


@pytest.mark.parametrize("filename", ["book.log", "book.shell.log"])
@pytest.mark.parametrize(
    "diagnostic",
    [
        "! Undefined control sequence.",
        "./book.tex:12: LaTeX Error: File `missing.sty' not found.",
        "Package fontspec Error: The font could not be found.",
        "Emergency stop.",
        "Fatal error occurred, no output PDF file produced!",
        "Latexmk: Errors, so I did not complete making targets",
        "Collected error summary (may duplicate other messages):",
    ],
)
def test_build_pdf_rejects_native_errors_with_readable_pdf(
    *, doc_root: Path, command: MagicMock, diagnostic: str, filename: str
) -> None:
    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        _write_output(doc_root=doc_root)
        (doc_root / "exports" / "book_pdf_logs" / filename).write_text(f"{diagnostic}\n", encoding="utf-8")
        return CompletedProcess([], 0)

    command.side_effect = render
    with pytest.raises(ValueError, match="Fatal LaTeX diagnostic"):
        build_docs_pdf.build_pdf(doc_root)


@pytest.mark.parametrize("filename", ["book.log", "book.shell.log"])
def test_build_pdf_requires_fresh_native_logs(*, doc_root: Path, command: MagicMock, filename: str) -> None:
    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        _write_output(doc_root=doc_root)
        (doc_root / "exports" / "book_pdf_logs" / filename).unlink()
        return CompletedProcess([], 0)

    command.side_effect = render
    with pytest.raises(ValueError, match="did not produce a nonempty file"):
        build_docs_pdf.build_pdf(doc_root)


def test_build_pdf_rejects_stale_logs_with_fresh_pdf(*, doc_root: Path, command: MagicMock) -> None:
    _write_output(doc_root=doc_root)

    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        writer = PdfWriter()
        writer.add_blank_page(width=144, height=144)
        writer.write(doc_root / "exports" / "book.pdf")
        return CompletedProcess([], 0)

    command.side_effect = render
    with pytest.raises(ValueError, match="left a stale file unchanged:.*book.log"):
        build_docs_pdf.build_pdf(doc_root)


def test_build_pdf_rejects_invalid_pdf(*, doc_root: Path, command: MagicMock) -> None:
    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        _write_output(doc_root=doc_root)
        (doc_root / "exports" / "book.pdf").write_bytes(b"not a PDF")
        return CompletedProcess([], 0)

    command.side_effect = render
    with pytest.raises(PdfReadError):
        build_docs_pdf.build_pdf(doc_root)


def test_build_pdf_rejects_empty_pdf(*, doc_root: Path, command: MagicMock) -> None:
    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        _write_output(doc_root=doc_root)
        (doc_root / "exports" / "book.pdf").write_bytes(b"")
        return CompletedProcess([], 0)

    command.side_effect = render
    with pytest.raises(ValueError, match="did not produce a nonempty file"):
        build_docs_pdf.build_pdf(doc_root)


def test_build_pdf_rejects_pdf_without_pages(*, doc_root: Path, command: MagicMock) -> None:
    def render(*args: object, **kwargs: object) -> CompletedProcess[bytes]:
        _write_output(doc_root=doc_root)
        PdfWriter().write(doc_root / "exports" / "book.pdf")
        return CompletedProcess([], 0)

    command.side_effect = render
    with pytest.raises(ValueError, match="PDF export contains no pages"):
        build_docs_pdf.build_pdf(doc_root)


@pytest.mark.parametrize(
    "config",
    [
        "null\n",
        "project: {}\n",
        "project:\n  exports: []\n",
        "project:\n  exports:\n    - format: pdf\n      template: other\n      output: book.pdf\n",
        "project:\n  exports:\n    - format: pdf\n      template: plain_latex_book\n",
        "project:\n  exports:\n    - format: pdf\n      template: plain_latex_book\n      output: ../book.pdf\n",
    ],
)
def test_build_pdf_rejects_invalid_config(*, doc_root: Path, command: MagicMock, config: str) -> None:
    (doc_root / "myst.yml").write_text(config, encoding="utf-8")
    with pytest.raises(ValueError):
        build_docs_pdf.build_pdf(doc_root)
    command.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Missing PDF prerequisites"),
        PdfReadError("Invalid PDF"),
        FileNotFoundError("Missing configuration"),
    ],
)
def test_main_reports_errors(*, capsys: pytest.CaptureFixture[str], error: Exception) -> None:
    with patch.object(build_docs_pdf, "build_pdf", side_effect=error):
        assert build_docs_pdf.main() == 1
    assert f"ERROR: {error}" in capsys.readouterr().err
