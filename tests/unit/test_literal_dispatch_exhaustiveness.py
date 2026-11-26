# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from unit.async_utils import TEST_WATCHDOG_SECONDS


@pytest.mark.parametrize(
    "module_name,options,guard_count",
    [
        ("pyrit.message_normalizer.message_normalizer", ("keep", "squash", "ignore"), 1),
        (
            "pyrit.converter.base64_converter",
            (
                "b64encode",
                "urlsafe_b64encode",
                "standard_b64encode",
                "b2a_base64",
                "b16encode",
                "b32encode",
                "a85encode",
                "b85encode",
            ),
            1,
        ),
        ("pyrit.converter.bin_ascii_converter", ("hex", "quoted-printable", "UUencode"), 2),
        ("pyrit.converter.pinyin_converter", ("full", "initial", "mixed"), 1),
        (
            "pyrit.converter.unicode_confusable_converter",
            ("confusable_homoglyphs", "confusables", "cyrillic_homoglyph"),
            1,
        ),
    ],
)
@pytest.mark.parametrize("extend_literal", [False, True], ids=["current-options", "extended-options"])
def test_literal_dispatch_exhaustiveness(
    tmp_path: Path, module_name: str, options: tuple[str, ...], guard_count: int, extend_literal: bool
) -> None:
    """Check the real dispatch source, then add an unhandled Literal option.

    Every matching annotation is extended together, including inline constructor
    and attribute annotations. The probe is type-checked, never executed, so it
    cannot call providers or mutate the imported module.
    """
    source = inspect.getsource(importlib.import_module(module_name))
    tree = ast.parse(source)
    matches = 0
    insertions: list[int] = []
    lines = source.encode("utf-8").splitlines(keepends=True)
    for node in ast.walk(tree):
        # Constructor validation must also accept the new supported option;
        # otherwise it narrows that option away before the separator dispatch.
        is_validation = module_name.endswith("bin_ascii_converter") and isinstance(node, ast.List)
        is_literal = isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal"
        if is_literal:
            values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        elif is_validation:
            values = node.elts
        else:
            continue
        if tuple(value.value if isinstance(value, ast.Constant) else None for value in values) != options:
            continue
        matches += int(is_literal)
        last = values[-1]
        assert last.end_lineno is not None and last.end_col_offset is not None
        insertions.append(sum(len(line) for line in lines[: last.end_lineno - 1]) + last.end_col_offset)
    assert matches, f"No matching Literal annotation found in {module_name}"

    probe_bytes = source.encode("utf-8")
    if extend_literal:
        for offset in sorted(insertions, reverse=True):
            probe_bytes = probe_bytes[:offset] + b', "future_option"' + probe_bytes[offset:]
    probe = tmp_path / f"{module_name.rsplit('.', 1)[-1]}.py"
    # Preserve comments, including existing third-party-stub type ignores.
    probe.write_bytes(probe_bytes)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ty",
            "check",
            "--project",
            str(Path(__file__).resolve().parents[2]),
            "--python-version",
            "3.11",
            str(probe),
        ],
        capture_output=True,
        text=True,
        timeout=TEST_WATCHDOG_SECONDS,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == (1 if extend_literal else 0), output
    if extend_literal:
        assert output.count("error[") == guard_count, output
        assert output.count("error[type-assertion-failure]") == guard_count, output
        assert "future_option" in output, output
