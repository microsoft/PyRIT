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


@pytest.fixture(params=["default", "python"])
def yaml_loader(request):
    real_import = builtins.__import__

    def _import_without_csafeloader(name: str, *args: Any, **kwargs: Any) -> Any:
        fromlist = args[2] if len(args) > 2 else kwargs.get("fromlist")
        if name == "yaml" and fromlist and "CSafeLoader" in fromlist:
            raise ImportError("libyaml is not available")
        return real_import(name, *args, **kwargs)

    try:
        if request.param == "python":
            with patch.object(builtins, "__import__", _import_without_csafeloader):
                importlib.reload(yaml_helper)
            assert yaml_helper.SafeLoader is yaml.SafeLoader
        yield yaml_helper.safe_load_yaml
    finally:
        importlib.reload(yaml_helper)


@pytest.mark.parametrize(
    "document, key, first_line, duplicate_line",
    [
        ("value: first\nvalue: second\n", "value", 1, 2),
        ("seeds: []\nseeds: []\n", "seeds", 1, 2),
        ("seeds:\n  - value: first\n    value: second\n", "value", 2, 3),
        ("metadata: {name: first, name: second}\n", "name", 1, 1),
        ("base: &base\n  value: first\n  value: second\ncopy: {<<: *base}\n", "value", 2, 3),
        ("derived:\n  <<: {value: base}\n  value: first\n  value: second\n", "value", 3, 4),
        ("derived:\n  <<: {value: first}\n  <<: {value: second}\n", "<<", 2, 3),
        ("1: first\n01: second\n", 1, 1, 2),
        ("null: first\n~: second\n", None, 1, 2),
        ("key: first\n'key': second\n", "key", 1, 2),
        ("true: first\n1: second\n", 1, 1, 2),
        ("1: first\ntrue: second\n", True, 1, 2),
        ("false: first\n0: second\n", 0, 1, 2),
        ("0: first\nfalse: second\n", False, 1, 2),
        (".nan: first\n.nan: second\n", float("nan"), 1, 2),
        (".NaN: first\n.NAN: second\n", float("nan"), 1, 2),
        # Alias nodes retain the anchor's source mark rather than the alias's line.
        ("&key value: first\n*key: second\n", "value", 1, 1),
        ("anchor: &key value\nvalue: first\n*key: second\n", "value", 2, 1),
    ],
)
def test_safe_load_yaml_rejects_explicit_duplicates(yaml_loader, document, key, first_line, duplicate_line):
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key") as error:
        yaml_loader(document)

    assert repr(key) in str(error.value)
    assert error.value.context_mark.line == first_line - 1
    assert error.value.problem_mark.line == duplicate_line - 1
    assert f"line {duplicate_line}" in str(error.value)


@pytest.mark.parametrize(
    "document",
    [
        "base: &base {value: base, extra: kept}\nderived: {<<: *base, value: explicit}\n",
        "base: &base {value: base}\nderived: {value: explicit, <<: *base}\n",
        (
            "first: &first {value: first}\nsecond: &second {value: second, extra: kept}\n"
            "derived: {<<: [*first, *second]}\n"
        ),
        (
            "base: &base {value: base}\nmid: &mid {<<: *base, value: middle}\n"
            "derived: {<<: *mid, value: explicit}\ncopy: {<<: *mid}\n"
        ),
        "derived: {<<: {value: base}, '<<': literal}\n",
        "one: {value: first}\ntwo: {value: second}\n",
        "=: value\n",
        "self: &self {reference: *self}\n",
        ".nan: first\nother: second\n",
        "first: {&key value: one}\nsecond: {*key: two}\n",
    ],
)
def test_safe_load_yaml_preserves_valid_mappings_and_merge_precedence(yaml_loader, document):
    assert repr(yaml_loader(document)) == repr(yaml.safe_load(document))


def test_safe_load_yaml_duplicate_stream_reports_filename(yaml_loader, tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text("value: first\nvalue: second\n", encoding="utf-8")
    with path.open(encoding="utf-8") as stream:
        with pytest.raises(yaml.YAMLError, match="duplicate key") as error:
            yaml_loader(stream)
    assert str(path) in str(error.value)


@pytest.mark.parametrize("document", ["key: [unterminated\n", "!!python/object/apply:os.system ['echo unsafe']\n"])
def test_safe_load_yaml_keeps_syntax_and_safe_tag_errors(yaml_loader, document):
    with pytest.raises(yaml.YAMLError):
        yaml_loader(document)


def test_safe_load_yaml_does_not_modify_pyyaml_loaders(yaml_loader):
    document = "value: first\nvalue: second\n"
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        yaml_loader(document)
    assert yaml.safe_load(document) == {"value": "second"}
    if hasattr(yaml, "CSafeLoader"):
        assert yaml.load(document, Loader=yaml.CSafeLoader) == {"value": "second"}
