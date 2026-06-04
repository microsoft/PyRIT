# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from build_scripts.check_no_rest_roles import main


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_main_passes_on_clean_file(tmp_path: Path) -> None:
    clean = _write(
        tmp_path,
        "clean.py",
        '"""Returns a ``SeedPrompt`` instance and calls ``download_files_async``."""\n',
    )
    assert main([str(clean)]) == 0


def test_main_passes_when_no_python_files(tmp_path: Path) -> None:
    md = _write(tmp_path, "notes.md", ":class:`Foo` is fine in markdown\n")
    # Non-Python paths are skipped without inspection.
    assert main([str(md)]) == 0


def test_main_flags_class_role(tmp_path: Path, capsys) -> None:
    bad = _write(tmp_path, "bad.py", '"""Returns a :class:`SeedPrompt` instance."""\n')
    rc = main([str(bad)])
    assert rc == 1
    err = capsys.readouterr().out
    assert "bad.py:1" in err
    assert ":class:`SeedPrompt`" in err


def test_main_flags_func_meth_and_py_prefixed_roles(tmp_path: Path) -> None:
    bad = _write(
        tmp_path,
        "bad.py",
        '"""\nSee :func:`do_thing` and :meth:`Foo.bar` and :py:class:`X`.\n"""\n',
    )
    assert main([str(bad)]) == 1


def test_main_ignores_bare_colon_in_code(tmp_path: Path) -> None:
    # ":key: value" pattern (e.g. Google docstring section header) should not match.
    clean = _write(
        tmp_path,
        "clean.py",
        '"""\nArgs:\n    foo (int): the foo value.\n"""\n',
    )
    assert main([str(clean)]) == 0


def test_main_returns_zero_when_called_without_args() -> None:
    assert main([]) == 0
