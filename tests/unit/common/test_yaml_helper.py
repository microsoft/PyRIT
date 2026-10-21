# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import builtins
import importlib
import io
from typing import Any
from unittest.mock import patch

import pytest
import yaml

import pyrit.common.yaml_helper as yaml_helper
from pyrit.common.yaml_helper import safe_load_yaml

REPRESENTATIVE_DOCUMENTS: list[str] = [
    "",
    "---\n",
    "name: example\nvalue: 42\n",
    "authors: Jane Doe\n",
    "authors:\n  - Jane Doe\n  - John Doe\n",
    "nested:\n  a:\n    b: [1, 2, 3]\n  c: {d: e}\n",
    "empty_value:\nnull_value: null\ntilde: ~\n",
    "bools: [true, false, yes, no, on, off]\n",
    "numbers: [1, -1, 1.5, 1e3, 0x10, 0o17]\n",
    "timestamp: 2024-01-02 03:04:05\ndate: 2024-01-02\n",
    "unicode: 'caf\u00e9 \u00fcber \u4f60\u597d \U0001f600'\n",
    "block: |\n  line one\n  line two\n",
    "folded: >\n  line one\n  line two\n",
    "anchored: &base\n  shared: 1\nderived:\n  <<: *base\n  extra: 2\n",
    'quoted: "a: b # not a comment"\n',
    "sexagesimal: 1:30\n",
    "special_floats: [.inf, -.inf, .nan]\n",
    "binary: !!binary |\n  R0lGODlhAQABAAAAACw=\n",
]


@pytest.mark.parametrize("document", REPRESENTATIVE_DOCUMENTS)
def test_safe_load_yaml_matches_safe_load_for_text(document: str) -> None:
    expected = yaml.safe_load(document)
    actual = safe_load_yaml(document)

    assert repr(actual) == repr(expected)


@pytest.mark.parametrize("document", REPRESENTATIVE_DOCUMENTS)
def test_safe_load_yaml_matches_safe_load_for_streams(document: str) -> None:
    expected = yaml.safe_load(io.StringIO(document))
    actual = safe_load_yaml(io.StringIO(document))

    assert repr(actual) == repr(expected)


def test_safe_load_yaml_accepts_bytes() -> None:
    assert safe_load_yaml("value: caf\u00e9\n".encode()) == {"value": "caf\u00e9"}


def test_safe_load_yaml_rejects_arbitrary_python_objects() -> None:
    with pytest.raises(yaml.YAMLError):
        safe_load_yaml("!!python/object/apply:os.system ['echo unsafe']\n")


def test_safe_load_yaml_raises_yaml_error_on_malformed_input() -> None:
    with pytest.raises(yaml.YAMLError):
        safe_load_yaml("key: [unterminated\n")


def test_safe_load_yaml_uses_c_loader_when_libyaml_is_available() -> None:
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("PyYAML was built without libyaml")

    assert yaml_helper.SafeLoader is yaml.CSafeLoader


def test_safe_load_yaml_falls_back_to_pure_python_loader_without_libyaml() -> None:
    real_import = builtins.__import__

    def _import_without_csafeloader(name: str, *args: Any, **kwargs: Any) -> Any:
        fromlist = args[2] if len(args) > 2 else kwargs.get("fromlist")
        if name == "yaml" and fromlist and "CSafeLoader" in fromlist:
            raise ImportError("libyaml is not available")
        return real_import(name, *args, **kwargs)

    try:
        with patch.object(builtins, "__import__", _import_without_csafeloader):
            reloaded = importlib.reload(yaml_helper)

        assert reloaded.SafeLoader is yaml.SafeLoader
        assert reloaded.safe_load_yaml("key: value\n") == {"key": "value"}
    finally:
        importlib.reload(yaml_helper)
