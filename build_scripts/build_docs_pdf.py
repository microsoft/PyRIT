# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Build the configured documentation PDF and verify the export, not just the exit code."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader
from pypdf.errors import PdfReadError


def _pdf_output(doc_root: Path) -> Path:
    config = yaml.safe_load((doc_root / "myst.yml").read_text(encoding="utf-8"))
    project = config.get("project") if isinstance(config, dict) else None
    exports = project.get("exports") if isinstance(project, dict) else None
    if not isinstance(exports, list):
        raise ValueError("myst.yml must declare a project PDF export.")
    pdf_exports = [export for export in exports if isinstance(export, dict) and export.get("format") == "pdf"]
    if len(pdf_exports) != 1:
        raise ValueError("Expected exactly one project PDF export in myst.yml.")
    export = pdf_exports[0]
    if export.get("template") != "plain_latex_book":
        raise ValueError("The checked PDF build supports plain_latex_book with xelatex.")
    output = export.get("output")
    if not isinstance(output, str) or not output or Path(output).suffix != ".pdf":
        raise ValueError("The PDF export must declare an output ending in .pdf.")
    output_path = (doc_root / output).resolve()
    if not output_path.is_relative_to(doc_root.resolve()):
        raise ValueError("The PDF output must stay inside the documentation directory.")
    return output_path


def _signature(path: Path) -> tuple[int, int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _require_fresh_file(*, path: Path, previous: tuple[int, int, int] | None) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"PDF export did not produce a nonempty file: {path}")
    if _signature(path) == previous:
        raise ValueError(f"PDF export left a stale file unchanged: {path}")


def _check_engine_log(path: Path) -> None:
    fatal = re.compile(
        r"^!|(?:LaTeX|Package \S+|Class \S+) Error:|Emergency stop|Fatal error occurred"
        r"|^Latexmk: (?:Errors|Failure)|^Collected error summary"
    )
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if fatal.search(line):
            raise ValueError(f"Fatal LaTeX diagnostic in {path}:{line_number}: {line}")


def build_pdf(doc_root: Path) -> int:
    """Build the repository's XeLaTeX PDF export and reject missing, stale, or failed output."""
    output = _pdf_output(doc_root)
    missing = [tool for tool in ("latexmk", "xelatex") if shutil.which(tool) is None]
    if missing:
        raise ValueError(
            f"Missing PDF prerequisites on PATH: {', '.join(missing)}. "
            "Provision LaTeX separately; use the HTML-only build if a PDF is not needed."
        )
    logs_dir = output.parent / f"{output.stem}_pdf_logs"
    logs = [logs_dir / f"{output.stem}.log", logs_dir / f"{output.stem}.shell.log"]
    previous = {path: _signature(path) for path in [output, *logs]}
    result = subprocess.run(
        [sys.executable, "-m", "jupyter_book", "build", "--site", "--pdf", "--strict", "--logs"],
        cwd=doc_root,
        check=False,
    )
    if result.returncode != 0:
        print(f"ERROR: Jupyter Book PDF build exited with code {result.returncode}.", file=sys.stderr)
        return result.returncode
    for path in [output, *logs]:
        _require_fresh_file(path=path, previous=previous[path])
    for log in logs:
        _check_engine_log(log)
    reader = PdfReader(output, strict=True)
    if not reader.pages:
        raise ValueError(f"PDF export contains no pages: {output}")
    print(f"[OK] Verified fresh PDF export and native LaTeX logs: {output}")
    return 0


def main() -> int:
    """Run the checked export from this checkout's documentation directory."""
    doc_root = Path(__file__).resolve().parents[1] / "doc"
    try:
        return build_pdf(doc_root)
    except (OSError, ValueError, yaml.YAMLError, PdfReadError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
