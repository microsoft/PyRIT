# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.common.path import ASSERT_BEHAVIORS_PATH, ASSERT_SUITES_PATH
from pyrit.datasets import (
    get_assert_behavior_preset,
    get_assert_suite_preset,
    list_assert_behavior_presets,
    list_assert_suite_presets,
)
from pyrit.datasets.assert_library import _load_behaviors, _load_suites
from pyrit.models import AssertBehaviorPreset, AssertGenerationConfig, AssertSuitePreset

_ATTRIBUTION = "# Copied from responsibleai/ASSERT v0.3.0"
_VENDORED_FILES = sorted([*ASSERT_BEHAVIORS_PATH.glob("*.yaml"), *ASSERT_SUITES_PATH.glob("*.yaml")])


def test_behavior_library_loads_every_vendored_behavior() -> None:
    presets = list_assert_behavior_presets()
    assert len(presets) == 52
    assert list(presets) == sorted(path.stem for path in ASSERT_BEHAVIORS_PATH.glob("*.yaml"))
    assert all(isinstance(preset, AssertBehaviorPreset) for preset in presets.values())
    assert all(name == preset.name for name, preset in presets.items())


def test_suite_library_loads_every_vendored_suite() -> None:
    suites = list_assert_suite_presets()
    assert len(suites) == 3
    assert list(suites) == sorted(path.stem for path in ASSERT_SUITES_PATH.glob("*.yaml"))
    assert all(isinstance(suite, AssertSuitePreset) for suite in suites.values())
    assert all(name == suite.name for name, suite in suites.items())


def test_every_suite_behavior_resolves_to_a_bundled_behavior() -> None:
    behaviors = list_assert_behavior_presets()
    for suite in list_assert_suite_presets().values():
        assert suite.behaviors
        for name in suite.behaviors:
            assert get_assert_behavior_preset(name) is behaviors[name]


@pytest.mark.parametrize("name", sorted(path.stem for path in ASSERT_BEHAVIORS_PATH.glob("*.yaml")))
def test_behavior_preset_builds_a_generation_request(name: str) -> None:
    preset = get_assert_behavior_preset(name)
    config = AssertGenerationConfig(behavior_name=preset.name, behavior_description=preset.description, group_count=1)
    assert config.behavior_description.strip()


def test_get_assert_suite_preset_returns_listed_suite() -> None:
    suites = list_assert_suite_presets()
    for name, suite in suites.items():
        assert get_assert_suite_preset(name) == suite
        assert suite.context.strip()


def test_listing_returns_a_copy() -> None:
    presets = list_assert_behavior_presets()
    name = next(iter(presets))
    del presets[name]
    assert name in list_assert_behavior_presets()


def test_get_assert_behavior_preset_unknown_name_lists_available_names() -> None:
    with pytest.raises(ValueError, match="No bundled ASSERT behavior is named 'not_a_behavior'") as error:
        get_assert_behavior_preset("not_a_behavior")
    assert str(sorted(list_assert_behavior_presets())) in str(error.value)


def test_get_assert_suite_preset_unknown_name_lists_available_names() -> None:
    with pytest.raises(ValueError, match="No bundled ASSERT suite is named 'not_a_suite'") as error:
        get_assert_suite_preset("not_a_suite")
    assert str(sorted(list_assert_suite_presets())) in str(error.value)


def test_suite_name_does_not_resolve_as_behavior() -> None:
    suite_name = next(iter(list_assert_suite_presets()))
    with pytest.raises(ValueError, match="No bundled ASSERT behavior"):
        get_assert_behavior_preset(suite_name)


@pytest.mark.parametrize("path", _VENDORED_FILES, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_vendored_yaml_keeps_attribution_header(path: Path) -> None:
    header = path.read_text(encoding="utf-8").splitlines()[:4]
    assert header[0] == "# Copyright (c) Microsoft Corporation."
    assert header[1] == "# Licensed under the MIT license."
    assert header[2].startswith(_ATTRIBUTION)
    assert header[2].endswith(f"/{path.name}")
    assert header[3] == "# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc."


def test_vendored_file_counts() -> None:
    assert len(list(ASSERT_BEHAVIORS_PATH.glob("*.yaml"))) == 52
    assert len(list(ASSERT_SUITES_PATH.glob("*.yaml"))) == 3


def _write_behavior(*, directory: Path, name: str, kind: str = "behavior", declared_name: str | None = None) -> None:
    (directory / f"{name}.yaml").write_text(
        f"kind: {kind}\nname: {declared_name or name}\nversion: '1.0'\nsummary: A summary.\ndescription: A behavior.\n",
        encoding="utf-8",
    )


@pytest.fixture
def fake_library(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    behaviors, suites = tmp_path / "behaviors", tmp_path / "suites"
    behaviors.mkdir()
    suites.mkdir()
    _load_behaviors.cache_clear()
    _load_suites.cache_clear()
    with (
        patch("pyrit.datasets.assert_library.ASSERT_BEHAVIORS_PATH", behaviors),
        patch("pyrit.datasets.assert_library.ASSERT_SUITES_PATH", suites),
    ):
        yield behaviors, suites
    _load_behaviors.cache_clear()
    _load_suites.cache_clear()


def test_suite_with_unknown_behavior_is_rejected(fake_library: tuple[Path, Path]) -> None:
    behaviors, suites = fake_library
    _write_behavior(directory=behaviors, name="known")
    (suites / "suite.yaml").write_text(
        "kind: scenario\nname: suite\nversion: '1.0'\nsummary: A suite.\ncontext: An app.\n"
        "behaviors: [known, missing]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"'suite' refers to unknown behaviors: \['missing'\]"):
        list_assert_suite_presets()
    assert list(list_assert_behavior_presets()) == ["known"]


@pytest.mark.parametrize(
    "kind, declared_name, message",
    [("scenario", None, "is not an ASSERT behavior preset"), ("behavior", "renamed", "must declare name 'known'")],
)
def test_malformed_behavior_file_is_rejected(
    fake_library: tuple[Path, Path], kind: str, declared_name: str | None, message: str
) -> None:
    behaviors, _ = fake_library
    _write_behavior(directory=behaviors, name="known", kind=kind, declared_name=declared_name)
    with pytest.raises(ValueError, match=message):
        list_assert_behavior_presets()


def test_empty_library_lists_nothing(fake_library: tuple[Path, Path]) -> None:
    assert list_assert_behavior_presets() == {}
    assert list_assert_suite_presets() == {}
    with pytest.raises(ValueError, match=r"Available: \[\]"):
        get_assert_behavior_preset("anything")
