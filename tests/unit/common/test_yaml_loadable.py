# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest

from pyrit.common.yaml_loadable import YamlLoadable


class _SimpleYaml(YamlLoadable):
    def __init__(self, name: str, value: int = 0) -> None:
        self.name = name
        self.value = value


class _WithFromDict(YamlLoadable):
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def from_dict(cls, data: dict) -> "_WithFromDict":
        return cls(name=data["name"].upper())


@pytest.fixture()
def yaml_file(tmp_path: Path) -> Path:
    p = tmp_path / "test.yaml"
    p.write_text("name: hello\nvalue: 42\n", encoding="utf-8")
    return p


@pytest.fixture()
def yaml_file_for_from_dict(tmp_path: Path) -> Path:
    p = tmp_path / "fd.yaml"
    p.write_text("name: lower\n", encoding="utf-8")
    return p


def test_from_yaml_file_basic(yaml_file: Path):
    obj = _SimpleYaml.from_yaml_file(yaml_file)
    assert obj.name == "hello"
    assert obj.value == 42


def test_from_yaml_file_uses_from_dict_if_available(yaml_file_for_from_dict: Path):
    obj = _WithFromDict.from_yaml_file(yaml_file_for_from_dict)
    assert obj.name == "LOWER"


def test_from_yaml_file_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        _SimpleYaml.from_yaml_file(Path("nonexistent_file.yaml"))


def test_from_yaml_file_invalid_yaml(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(":\n  - :\n    invalid: [unterminated", encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        _SimpleYaml.from_yaml_file(p)
