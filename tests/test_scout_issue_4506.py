# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

_EXPECTED_GUARDS = {
    "pyrit/executor/attack/compound/sequential_attack.py": 2,
    "pyrit/executor/promptgen/assert_generator.py": 3,
    "pyrit/prompt_target/openai/openai_response_target.py": 1,
    "pyrit/auth/openai_auth.py": 1,
}


def _extend_enum(project: Path, module: str, enum: str) -> None:
    path = project / module
    source = path.read_text(encoding="utf-8")
    definition = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == enum)
    assert definition.end_lineno is not None
    lines = source.splitlines(keepends=True)
    lines.insert(definition.end_lineno, '    FUTURE = "future"\n')
    path.write_text("".join(lines), encoding="utf-8")


@pytest.mark.parametrize("extend_types", [False, True], ids=["current-types", "extended-types"])
def test_batch_c_dispatch_exhaustiveness(tmp_path: Path, *, extend_types: bool) -> None:
    """Adding enum members and a literal must surface a diagnostic at all seven dispatch guards."""
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "pyrit", tmp_path / "pyrit", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(root / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "tests").mkdir()  # The ty configuration includes this directory in its search path.
    if extend_types:
        _extend_enum(tmp_path, "pyrit/executor/attack/compound/sequential_attack.py", "SequenceCompletionPolicy")
        _extend_enum(tmp_path, "pyrit/models/seeds/assert_generation.py", "AssertGenerationStage")
        _extend_enum(tmp_path, "pyrit/models/seeds/assert_generation.py", "AssertSamplingMethod")
        _extend_enum(tmp_path, "pyrit/models/target/web_search_config.py", "WebSearchMode")
        auth = tmp_path / "pyrit/common/auth_mode.py"
        lines = auth.read_text(encoding="utf-8").splitlines(keepends=True)
        index = next(index for index, line in enumerate(lines) if line.startswith("AuthMode = Literal["))
        lines[index] = lines[index].rstrip().removesuffix("]") + ', "future"]\n'
        auth.write_text("".join(lines), encoding="utf-8")

    result = subprocess.run(
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
            *_EXPECTED_GUARDS,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    if not extend_types:
        assert result.returncode == 0, output
        return

    assert result.returncode in (0, 1), output
    diagnostics = Counter(
        Path(line.split(":", 1)[0]).as_posix()
        for line in output.splitlines()
        if "error[type-assertion-failure]" in line and "future" in line.lower()
    )
    assert diagnostics == _EXPECTED_GUARDS, f"OSS_SCOUT_REGRESSION: {diagnostics}\n{output}"
