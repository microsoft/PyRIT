# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from frontend.e2e.fixtures.scenario_dataset_backend import _configure_preset_storage
from pyrit.models import ScenarioPreset
from pyrit.registry import ScenarioPresetConflictError, ScenarioPresetRegistry, ScenarioPresetStorage


@pytest.fixture
def guarded_presets(tmp_path: Path) -> Iterator[tuple[Path, ScenarioPresetRegistry, list[dict[str, str]]]]:
    root = tmp_path / "fixture"
    registry = ScenarioPresetRegistry()
    mutations: list[dict[str, str]] = []
    with ExitStack() as patches:
        patches.enter_context(patch.object(ScenarioPresetRegistry, "get_registry_singleton", return_value=registry))
        _configure_preset_storage(
            database_path=str(root / "dbdata" / "memory.db"), patches=patches, mutations=mutations
        )
        yield root, registry, mutations


def test_preset_guard_delegates_real_save_and_delete(
    guarded_presets: tuple[Path, ScenarioPresetRegistry, list[dict[str, str]]],
) -> None:
    root, registry, mutations = guarded_presets
    preset = ScenarioPreset(name="guarded", scenario_name="offline.datasets")
    saved = registry.save_preset(preset=preset, expected_version=None)
    document = root / "scenario_presets" / "guarded.json"
    assert saved.preset == preset
    assert document.is_file()
    with pytest.raises(ScenarioPresetConflictError, match="changed by someone else"):
        registry.delete_preset(name=preset.name, expected_version="stale")
    assert document.is_file()
    assert registry.delete_preset(name=preset.name, expected_version=saved.version)
    assert not document.exists()
    assert [mutation["action"] for mutation in mutations] == ["save", "delete", "delete"]
    for mutation in mutations:
        assert all(Path(mutation[field]).is_relative_to(root) for field in ("source", "document", "lock"))


@pytest.mark.parametrize("escaped_path", ["source", "document", "lock"])
@pytest.mark.parametrize("action", ["save", "delete"])
def test_preset_guard_rejects_escaped_paths_before_mutation(
    *,
    guarded_presets: tuple[Path, ScenarioPresetRegistry, list[dict[str, str]]],
    escaped_path: str,
    action: str,
) -> None:
    root, _, mutations = guarded_presets
    outside = root.parent / "outside" / "guarded.json"
    storage = ScenarioPresetStorage(
        source=str(outside.parent if escaped_path == "source" else root / "scenario_presets")
    )
    with ExitStack() as patches:
        saving = patches.enter_context(patch.object(storage, "_save_document_conditional", autospec=True))
        deleting = patches.enter_context(patch.object(storage, "_delete_document", autospec=True))
        if escaped_path == "document":
            patches.enter_context(patch.object(storage, "_get_document_source", return_value=str(outside)))
        elif escaped_path == "lock":
            original_resolve = Path.resolve
            lock = root / "scenario_presets" / ".guarded.json.lock"

            def resolve(self: Path, strict: bool = False) -> Path:
                return outside if self == lock else original_resolve(self, strict=strict)

            patches.enter_context(patch.object(Path, "resolve", resolve))
        with pytest.raises(RuntimeError, match="mutation attempted to leave the fixture-owned directory"):
            if action == "save":
                storage.save_preset(
                    preset=ScenarioPreset(name="guarded", scenario_name="offline.datasets"), expected_version=None
                )
            else:
                storage.delete_preset("guarded")
        saving.assert_not_called()
        deleting.assert_not_called()
        assert mutations == []
        assert not outside.exists()
