# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TY_TIMEOUT_SECONDS = 300

# Batch C of the assert_never sweep (#4506): executor and prompt_target dispatches,
# with the expected guard count per file.
_BATCH_C_GUARDS = {
    "pyrit/auth/openai_auth.py": 1,
    "pyrit/executor/attack/compound/sequential_attack.py": 2,
    "pyrit/executor/promptgen/assert_generator.py": 3,
    "pyrit/prompt_target/openai/openai_response_target.py": 1,
}


def _extend_enum(project: Path, module: str, enum: str) -> None:
    path = project / module
    source = path.read_text(encoding="utf-8")
    definition = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == enum)
    assert definition.end_lineno is not None
    lines = source.splitlines(keepends=True)
    lines.insert(definition.end_lineno, '    FUTURE = "future"\n')
    path.write_text("".join(lines), encoding="utf-8")


def _extend_auth_mode_literal(project: Path) -> None:
    path = project / "pyrit/common/auth_mode.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    index = next(index for index, line in enumerate(lines) if line.startswith("AuthMode = Literal["))
    lines[index] = lines[index].rstrip().removesuffix("]") + ', "future"]\n'
    path.write_text("".join(lines), encoding="utf-8")


def _run_ty(project: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ty",
            "check",
            "--python-version",
            "3.11",
            "--output-format",
            "concise",
            "--ignore",
            "all",
            "--error",
            "type-assertion-failure",
            *_BATCH_C_GUARDS,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=_TY_TIMEOUT_SECONDS,
        check=False,
    )


def test_batch_c_dispatch_guards_fire_on_extended_types(tmp_path: Path) -> None:
    """Adding enum members (and an ``AuthMode`` literal) must trip every Batch C dispatch guard."""
    project = tmp_path / "project"
    shutil.copytree(_REPO_ROOT / "pyrit", project / "pyrit", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(_REPO_ROOT / "pyproject.toml", project / "pyproject.toml")
    (project / "tests").mkdir()  # The ty configuration lists this directory as an extra search path.

    current = _run_ty(project)
    assert current.returncode == 0, current.stdout + current.stderr

    _extend_enum(project, "pyrit/executor/attack/compound/sequential_attack.py", "SequenceCompletionPolicy")
    _extend_enum(project, "pyrit/models/seeds/assert_generation.py", "AssertGenerationStage")
    _extend_enum(project, "pyrit/models/seeds/assert_generation.py", "AssertSamplingMethod")
    _extend_enum(project, "pyrit/models/target/web_search_config.py", "WebSearchMode")
    _extend_auth_mode_literal(project)

    extended = _run_ty(project)
    output = extended.stdout + extended.stderr
    assert extended.returncode == 1, output
    diagnostics = Counter(
        Path(line.split(":", 1)[0]).as_posix()
        for line in output.splitlines()
        if "error[type-assertion-failure]" in line and "future" in line.lower()
    )
    assert diagnostics == Counter(_BATCH_C_GUARDS), output
