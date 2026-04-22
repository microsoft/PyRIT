# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.show_versions import _get_deps_info, show_versions


def test_get_deps_info_contains_pyrit():
    result = _get_deps_info()
    assert "pyrit" in result
    assert result["pyrit"] is not None


def test_get_deps_info_contains_known_deps():
    result = _get_deps_info()
    assert "openai" in result
    assert "numpy" in result


def test_show_versions_prints_output(capsys):
    show_versions()
    captured = capsys.readouterr()
    assert "System:" in captured.out
    assert "Python dependencies:" in captured.out
    assert "pyrit" in captured.out
    assert "python" in captured.out
