# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Literal

import pytest

from frontend.e2e.fixtures.scenario_techniques_backend import _guard_preset_paths
from pyrit.models import ScenarioPreset
from pyrit.registry import ScenarioPresetStorage

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_refuses_outside_preset_mutations_before_creating_a_directory(
    *, tmp_path: Path, operation: Literal["save", "delete"]
) -> None:
    outside = tmp_path / "outside"
    storage = ScenarioPresetStorage(source=str(outside))
    preset = ScenarioPreset(name="technique_guard", scenario_name="technique_acceptance")

    with ExitStack() as patches:
        _guard_preset_paths(patches=patches, directory=tmp_path / "owned")
        with pytest.raises(RuntimeError, match="Refusing preset storage outside the fixture"):
            if operation == "save":
                storage.save_preset(preset=preset, expected_version=None)
            else:
                storage.delete_preset(preset.name)

    assert not outside.exists()


def test_allows_contained_preset_writes_and_lock_sidecars(*, tmp_path: Path) -> None:
    root = tmp_path / "owned"
    source = root / "presets"
    storage = ScenarioPresetStorage(source=str(source))
    preset = ScenarioPreset(name="technique_guard", scenario_name="technique_acceptance")

    with ExitStack() as patches:
        _guard_preset_paths(patches=patches, directory=root)
        stored = storage.save_preset(preset=preset, expected_version=None)
        assert stored.preset == preset
        assert (source / "technique_guard.json").is_file()
        assert (source / ".technique_guard.json.lock").is_file()
        assert storage.delete_preset(preset.name)
        assert not (source / "technique_guard.json").exists()
        assert (source / ".technique_guard.json.lock").is_file()
