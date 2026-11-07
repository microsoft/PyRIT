# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap
from pathlib import Path

import pytest

from build_scripts.check_keyword_only_params import (
    main,
    read_baseline,
    scan_repo,
    write_baseline,
)


def _write_module(repo_root: Path, *, rel_path: str, source: str) -> Path:
    path = repo_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _keys(repo_root: Path) -> list[str]:
    return [violation.key for violation in scan_repo(repo_root=repo_root)]


def _run(repo_root: Path, *extra: str) -> int:
    return main(["--repo-root", str(repo_root), "--baseline", str(repo_root / "baseline.txt"), *extra])


@pytest.mark.parametrize(
    "source",
    [
        "def f(a): ...\n",
        "def f(a, *, b): ...\n",
        "def f(*, a, b): ...\n",
        "def f(a, *args, b): ...\n",
        "def f(a, **kwargs): ...\n",
        "async def f_async(a, *, b): ...\n",
        "class C:\n    def m(self, a, *, b): ...\n",
        "class C:\n    @classmethod\n    def m(cls, a, *, b): ...\n",
        "class C:\n    def __init__(self, *, a, b): ...\n",
        "lambda_like = lambda a, b: a + b\n",
    ],
)
def test_scan_accepts_at_most_one_positional_parameter(tmp_path: Path, source: str) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source=source)
    assert _keys(tmp_path) == []


@pytest.mark.parametrize(
    ("source", "expected_key"),
    [
        ("def f(a, b): ...\n", "pyrit.mod:f"),
        ("def f(a, b=1): ...\n", "pyrit.mod:f"),
        ("def f(a, b, /): ...\n", "pyrit.mod:f"),
        ("def f(a, /, b): ...\n", "pyrit.mod:f"),
        ("async def f_async(a, b): ...\n", "pyrit.mod:f_async"),
        ("def _private(a, b): ...\n", "pyrit.mod:_private"),
        ("class C:\n    def m(self, a, b): ...\n", "pyrit.mod:C.m"),
        ("class C:\n    @classmethod\n    def m(cls, a, b): ...\n", "pyrit.mod:C.m"),
        ("class C:\n    def __init__(self, a, b): ...\n", "pyrit.mod:C.__init__"),
        ("class C:\n    def __new__(cls, a, b): ...\n", "pyrit.mod:C.__new__"),
        ("class Outer:\n    class Inner:\n        def m(self, a, b): ...\n", "pyrit.mod:Outer.Inner.m"),
        ("def outer():\n    def inner(a, b): ...\n", "pyrit.mod:outer.<locals>.inner"),
    ],
)
def test_scan_flags_more_than_one_positional_parameter(tmp_path: Path, source: str, expected_key: str) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source=source)
    assert _keys(tmp_path) == [expected_key]


def test_scan_counts_staticmethod_first_parameter(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        class C:
            @staticmethod
            def one(a): ...

            @staticmethod
            def two(a, b): ...
        """,
    )
    assert _keys(tmp_path) == ["pyrit.mod:C.two"]


def test_scan_does_not_treat_nested_function_in_method_as_method(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        class C:
            def m(self):
                def helper(a, b): ...
        """,
    )
    assert _keys(tmp_path) == ["pyrit.mod:C.m.<locals>.helper"]


def test_scan_uses_package_name_for_init_module(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="pyrit/pkg/__init__.py", source="def f(a, b): ...\n")
    assert _keys(tmp_path) == ["pyrit.pkg:f"]


@pytest.mark.parametrize(
    "source",
    [
        "class C:\n    def __exit__(self, exc_type, exc, tb): ...\n",
        "class C:\n    async def __aexit__(self, exc_type, exc, tb): ...\n",
        "class C:\n    def __eq__(self, other, extra=None): ...\n",
        "class C:\n    def __call__(self, a, b): ...\n",
        "class M:\n    async def dispatch(self, request, call_next): ...\n",
        "class T:\n    def process_bind_param(self, value, dialect): ...\n",
        "class T:\n    def process_result_value(self, value, dialect): ...\n",
        "from typing import overload\n\n@overload\ndef f(a: int, b: int) -> int: ...\n",
        "import typing\n\n@typing.overload\ndef f(a: int, b: int) -> int: ...\n",
    ],
)
def test_scan_exempts_dunders_framework_names_and_overloads(tmp_path: Path, source: str) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source=source)
    assert _keys(tmp_path) == []


def test_scan_does_not_exempt_override_decorator(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        from typing import override

        class C:
            @override
            def m(self, a, b): ...
        """,
    )
    assert _keys(tmp_path) == ["pyrit.mod:C.m"]


def test_scan_honours_exempt_marker_on_def_line(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="def handler(signum, frame): ...  # pyrit-keyword-only-exempt signal handler\n",
    )
    assert _keys(tmp_path) == []


def test_scan_honours_exempt_marker_in_multiline_header(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        @decorator
        def handler(  # pyrit-keyword-only-exempt argparse type= callback
            value: str,
            other: str,
        ) -> str:
            return value
        """,
    )
    assert _keys(tmp_path) == []


def test_scan_ignores_marker_inside_body(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        def f(a, b):
            x = 1
            return x  # pyrit-keyword-only-exempt
        """,
    )
    assert _keys(tmp_path) == ["pyrit.mod:f"]


def test_scan_ignores_marker_before_first_body_statement(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        def f(a, b):
            # pyrit-keyword-only-exempt
            return a
        """,
    )
    assert _keys(tmp_path) == ["pyrit.mod:f"]


def test_scan_honours_exempt_marker_on_closing_line_with_inline_body(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        def handler(
            value: str,
            other: str,
        ): pass  # pyrit-keyword-only-exempt callback
        """,
    )
    assert _keys(tmp_path) == []


def test_scan_finds_header_end_past_colons_in_defaults_and_annotations(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        rel_path="pyrit/mod.py",
        source="""
        class C:
            def m(
                self,
                key=lambda item: item[1:2],
                other: dict[str, int] = {"a": 1},
            ) -> dict[str, int]:  # pyrit-keyword-only-exempt callback
                # pyrit-keyword-only-exempt
                return {}

            def n(self, a=lambda item: item, b=1):
                # pyrit-keyword-only-exempt
                return {}
        """,
    )
    assert _keys(tmp_path) == ["pyrit.mod:C.n"]


def test_scan_only_covers_pyrit_package(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="tests/test_mod.py", source="def test_f(fixture_a, fixture_b): ...\n")
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="def f(a): ...\n")
    assert _keys(tmp_path) == []


def test_scan_results_are_sorted_by_key(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="pyrit/b.py", source="def z(a, b): ...\ndef a(a, b): ...\n")
    _write_module(tmp_path, rel_path="pyrit/a.py", source="def f(a, b): ...\n")
    assert _keys(tmp_path) == ["pyrit.a:f", "pyrit.b:a", "pyrit.b:z"]


def test_main_passes_on_clean_repo(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="def f(a, *, b): ...\n")
    assert _run(tmp_path) == 0


def test_main_reports_new_violation_with_location(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="\n\ndef f(a, b): ...\n")
    assert _run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "pyrit/mod.py:3: pyrit.mod:f(a, b)" in out
    assert "# pyrit-keyword-only-exempt" in out


def test_main_passes_when_violation_is_baselined(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="def f(a, b): ...\n")
    (tmp_path / "baseline.txt").write_text("# comment\n\npyrit.mod:f\n", encoding="utf-8")
    assert _run(tmp_path) == 0


def test_main_baseline_is_keyed_by_qualname_not_line(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="def f(a, b): ...\n")
    assert _run(tmp_path, "--write-baseline") == 0
    # Moving the function within the file does not invalidate the baseline entry.
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="X = 1\n\n\ndef f(a, b): ...\n")
    assert _run(tmp_path) == 0


def test_main_flags_new_violation_alongside_baselined_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="def old(a, b): ...\ndef new(a, b): ...\n")
    (tmp_path / "baseline.txt").write_text("pyrit.mod:old\n", encoding="utf-8")
    assert _run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "pyrit.mod:new" in out
    assert "pyrit.mod:old" not in out


def test_main_fails_on_stale_baseline_entry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_module(tmp_path, rel_path="pyrit/mod.py", source="def f(a, *, b): ...\n")
    (tmp_path / "baseline.txt").write_text("pyrit.mod:f\n", encoding="utf-8")
    assert _run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "no longer violate" in out
    assert "pyrit.mod:f" in out


def test_write_baseline_is_sorted_deduplicated_and_round_trips(tmp_path: Path) -> None:
    _write_module(tmp_path, rel_path="pyrit/b.py", source="def f(a, b): ...\n")
    _write_module(
        tmp_path,
        rel_path="pyrit/a.py",
        source="import sys\n\nif sys.platform == 'win32':\n    def g(a, b): ...\nelse:\n    def g(a, b): ...\n",
    )
    baseline = tmp_path / "baseline.txt"
    write_baseline(baseline, violations=scan_repo(repo_root=tmp_path))

    lines = baseline.read_text(encoding="utf-8").splitlines()
    entries = [line for line in lines if line and not line.startswith("#")]
    assert entries == ["pyrit.a:g", "pyrit.b:f"]
    assert read_baseline(baseline) == {"pyrit.a:g", "pyrit.b:f"}
    assert b"\r\n" not in baseline.read_bytes()


def test_read_baseline_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_baseline(tmp_path / "missing.txt") == set()


def test_main_reports_syntax_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_module(tmp_path, rel_path="pyrit/broken.py", source="def f(:\n")
    assert _run(tmp_path) == 1
    assert "could not parse" in capsys.readouterr().out
